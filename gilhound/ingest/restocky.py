"""Restocky listing snapshots and confirmed sales. JSON wins over its CSV mirror."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gilhound.models import LedgerEventKind, SourceName

from .common import (
    READ_ERRORS,
    Records,
    Source,
    Stock,
    csv_rows,
    event,
    item_ref,
    read_source,
    require,
    uint,
)

HEADERS = {
    "listings.csv": (
        "listing_id retainer item_id item_name hq qty first_seen_utc first_price last_price "
        "reprice_count undercut_count undercutters competitors ended_utc outcome sold_total_gil "
        "days_listed buyer sale_time_utc sold_gross_gil is_mannequin cost profit"
    ),
    "competitors.csv": (
        "item_id item_name hq competitor times_seen times_below_us lowest_price last_qty max_qty "
        "first_seen_utc last_seen_utc listings_contested"
    ),
    "price-history.csv": (
        "utc_time retainer slot item_id hq qty old_price lowest_competitor new_price applied "
        "reason undercutters"
    ),
}
TEXT = set(
    (
        "listing_id retainer item_name undercutters competitors outcome buyer competitor reason"
    ).split()
)
OPTIONAL = set(
    (
        "first_seen_utc first_price last_price ended_utc sold_total_gil days_listed "
        "sale_time_utc sold_gross_gil is_mannequin cost profit lowest_competitor"
    ).split()
)


def utc(value: str) -> int:
    dt = datetime.fromisoformat(value)
    return int((dt if dt.tzinfo else dt.replace(tzinfo=UTC)).timestamp())


def flag(value: object) -> bool:
    require(str(value).lower() in ("true", "false"))
    return str(value).lower() == "true"


def table(path: Path) -> Iterator[dict[str, Any]]:
    rows = csv_rows(path)
    header = next(rows, [])
    require(header == HEADERS[path.name].split())
    for row in rows:
        data: dict[str, Any] = {}
        for key, value in zip(header, row, strict=True):
            if key in TEXT:
                data[key] = value
            elif value == "" and key in OPTIONAL:
                data[key] = None
            elif key in ("hq", "applied", "is_mannequin"):
                data[key] = flag(value)
            elif key.endswith("_utc") or key == "utc_time":
                data[key] = utc(value)
            elif key == "days_listed":
                data[key] = float(value)
            elif key == "profit":
                data[key] = int(value)
            else:
                data[key] = uint(value)
        yield data


def objects(path: Path) -> list[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8-sig"))
    require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows))
    return rows


def json_listing(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "item_id": uint(row["ItemId"]),
        "item_name": row["ItemName"],
        "hq": flag(row["IsHq"]),
        "qty": uint(row["Quantity"]),
        "outcome": row["Outcome"],
        "retainer": row["RetainerName"],
        "last_price": uint(row["LastPrice"]),
        "first_seen_utc": utc(row["FirstSeenUtc"]),
        "sale_time_utc": utc(row["SaleTimeUtc"]) if row["SaleTimeUtc"] else None,
        "ended_utc": utc(row["EndedUtc"]) if row["EndedUtc"] else None,
        "sold_total_gil": uint(row["SoldTotalGil"]),
    }


def listings(records: Records, rows: Iterator[dict[str, Any]]) -> None:
    records.listings = []
    for row in rows:
        item = item_ref(row["item_id"], row["hq"], row["item_name"])
        qty = row["qty"]
        require(qty > 0)
        outcome = row["outcome"]
        if outcome == "active":
            require(row["last_price"] is not None and row["first_seen_utc"] is not None)
            records.listings.append(
                Stock(item, qty, row["last_price"], row["retainer"], ts=row["first_seen_utc"])
            )
        elif outcome == "sold":
            ts = row["sale_time_utc"] or row["ended_utc"]
            require(ts is not None and row["sold_total_gil"] is not None)
            records.events.append(
                event(
                    SourceName.RESTOCKY, LedgerEventKind.SELL, item, qty, ts, row["sold_total_gil"]
                )
            )
        else:
            require(outcome == "gone")


def parse(paths: dict[str, Path]) -> Records:
    records = Records()
    if "listings.json" in paths:
        listings(records, map(json_listing, objects(paths["listings.json"])))
        records.listings_mtime = int(paths["listings.json"].stat().st_mtime)
        if "listings.csv" in paths:
            try:
                listings(Records(), table(paths["listings.csv"]))
            except READ_ERRORS:
                records.warning = "Restocky listings.csv mirror could not be read"
    elif "listings.csv" in paths:
        listings(records, table(paths["listings.csv"]))
        records.listings_mtime = int(paths["listings.csv"].stat().st_mtime)
    if "cost-ledger.json" in paths:
        for row in objects(paths["cost-ledger.json"]):
            item = item_ref(uint(row["ItemId"]), flag(row["IsHq"]))
            records.remaining_costs.append(
                Stock(
                    item, uint(row["RemainingQuantity"]), uint(row["UnitCost"]), ts=utc(row["Utc"])
                )
            )
    if "competitors.csv" in paths:
        records.competitors = list(table(paths["competitors.csv"]))
    if "price-history.csv" in paths:
        records.price_history = list(table(paths["price-history.csv"]))
    return records


def read(folder: Path) -> Source:
    return read_source(
        folder,
        SourceName.RESTOCKY,
        "Restocky",
        (*HEADERS, "listings.json", "cost-ledger.json"),
        parse,
    )
