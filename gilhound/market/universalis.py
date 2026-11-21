"""Convert Universalis wire data to market models at the HTTP boundary."""

import hashlib
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlencode

from gilhound.market.http import HttpClient
from gilhound.models import DataCenter, Listing, Sale, World

BASE = "https://universalis.app/api/v2"
LIST_FIELDS = (
    "retainerName",
    "worldName",
    "worldID",
    "pricePerUnit",
    "quantity",
    "hq",
    "lastReviewTime",
)


def epoch(value: int | float) -> int:
    return int(value // 1000 if value >= 10**12 else value)


def identity(name: str) -> str:
    return hashlib.sha256(name.casefold().encode()).hexdigest() if name else ""


def sale(row: dict[str, Any]) -> Sale:
    return Sale(
        ppu=row["pricePerUnit"],
        qty=row["quantity"],
        hq=row["hq"],
        ts=epoch(row["timestamp"]),
        buyer_hash=identity(row.get("buyerName") or ""),
    )


@dataclass
class Snapshot:
    listings: list[Listing]
    sales: list[Sale]
    upload_ts: int
    world_upload_ts: dict[str, int]
    velocity: dict[bool, float] = field(default_factory=dict)


class Universalis:
    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def marketable(self) -> list[int]:
        return self.http.request(f"{BASE}/marketable")

    def data_centers(self) -> list[DataCenter]:
        names = {w["id"]: w["name"] for w in self.http.request(f"{BASE}/worlds")}
        return [
            DataCenter(
                name=d["name"],
                region=d["region"],
                worlds=[
                    World(id=i, name=names[i], dc=d["name"]) for i in d["worlds"] if i in names
                ],
            )
            for d in self.http.request(f"{BASE}/data-centers")
        ]

    def current(
        self,
        location: str,
        ids: list[int],
        *,
        listings: int | None = None,
        entries: int = 0,
        hq: bool | None = None,
        projected: bool = False,
        worlds: dict[int, str] | None = None,
    ) -> dict[int, Snapshot]:
        if not 1 <= len(ids) <= 100:
            raise ValueError("Current listings require 1–100 IDs")
        query: dict[str, str | int] = {}
        if listings is not None:
            query["listings"] = listings
        query["entries"] = entries
        if hq is not None:
            query["hq"] = str(hq).lower()
        if projected:
            prefix = "items." if len(ids) > 1 else ""
            query["fields"] = ",".join(
                [*(f"{prefix}listings.{k}" for k in LIST_FIELDS), "unresolvedItems"]
            )
        path = ",".join(map(str, ids))
        raw = self.http.request(f"{BASE}/{quote(location, safe='')}/{path}?{urlencode(query)}")
        items = raw.get("items", {str(ids[0]): raw} if len(ids) == 1 else {})
        unresolved = set(raw.get("unresolvedItems", []))
        results = {}
        for key, item in items.items():
            if int(key) in unresolved:
                continue
            rows = []
            for row in item.get("listings", []):
                if hq is not None and bool(row["hq"]) != hq:
                    continue
                name = row.get("retainerName") or ""
                world = row.get("worldName") or (worlds or {}).get(row.get("worldID"), location)
                rows.append(
                    Listing(
                        world=world,
                        ppu=row["pricePerUnit"],
                        qty=row["quantity"],
                        hq=row["hq"],
                        retainer_name=name,
                        retainer_hash=identity(name),
                        last_review_ts=epoch(row.get("lastReviewTime") or 0),
                    )
                )
            results[int(key)] = Snapshot(
                sorted(rows, key=lambda row: row.ppu),
                [sale(s) for s in item.get("recentHistory", []) if hq is None or s["hq"] == hq],
                epoch(item.get("lastUploadTime") or 0),
                {k: epoch(v) for k, v in item.get("worldUploadTimes", {}).items()},
                {False: item.get("nqSaleVelocity") or 0, True: item.get("hqSaleVelocity") or 0},
            )
        return results

    def history(
        self, location: str, item: int, *, min_price: bool = False, entries: int = 80,
        within: int = 1209600,
    ) -> list[Sale]:
        path = f"{BASE}/history/{quote(location, safe='')}/{item}"
        url = f"{path}?entriesToReturn={entries}&entriesWithin={within}"
        raw = self.http.request(url + ("&minSalePrice=0" if min_price else ""))
        return [sale(row) for row in raw.get("entries", [])]
