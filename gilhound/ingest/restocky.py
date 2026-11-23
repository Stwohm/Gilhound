"""Restocky listing snapshots and confirmed sales. JSON wins over its CSV mirror."""

import json
from collections import Counter, defaultdict
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gilhound.models import LedgerEventKind, LowStockItems, SaleDetails, SearchEntry, SourceName

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
    require(isinstance(row.get("Competitors", {}), dict))
    rivals = {name: uint(stat["TimesBelowUs"]) for name, stat in row.get("Competitors", {}).items()
              if isinstance(stat, dict)}
    require(len(rivals) == len(row.get("Competitors", {})))
    sale_time = row["SaleTimeUtc"] or row.get("SoldRecordedUtc")
    return {
        "slot": int(row.get("Slot", -1)),
        "reprice_count": uint(row["RepriceCount"]),
        "undercut_count": uint(row["UndercutCount"]),
        "rivals": rivals,
        "history_only": flag(row.get("HistoryOnly", False)),
        "sold_gross_gil": None if row.get("SoldGrossGil") is None else uint(row["SoldGrossGil"]),
        "item_id": uint(row["ItemId"]),
        "item_name": row["ItemName"],
        "hq": flag(row["IsHq"]),
        "qty": uint(row["Quantity"]),
        "outcome": row["Outcome"],
        "retainer": row["RetainerName"],
        "last_price": uint(row["LastPrice"]),
        "first_seen_utc": utc(row["FirstSeenUtc"]),
        "sale_time_utc": utc(sale_time) if sale_time else None,
        "ended_utc": utc(row["EndedUtc"]) if row["EndedUtc"] else None,
        "sold_total_gil": uint(row["SoldTotalGil"]),
    }


def listings(records: Records, rows: Iterator[dict[str, Any]]) -> None:
    records.listings = []
    prices: dict[tuple, list[dict]] = defaultdict(list)
    for price in records.price_history:
        prices[price["item_id"], price["hq"], price["retainer"], price["slot"]].append(price)
    for row in rows:
        item = item_ref(row["item_id"], row["hq"], row["item_name"])
        qty = row["qty"]
        require(qty > 0)
        outcome = row["outcome"]
        history = prices.get((item.id, item.hq, row["retainer"], row.get("slot")), [])
        if outcome == "active":
            require(row["last_price"] is not None and row["first_seen_utc"] is not None)
            last = max((r for r in history
                        if r["utc_time"] >= row["first_seen_utc"]
                        and row["last_price"] == r["new_price" if r["applied"] else "old_price"]
                        ), key=lambda r: r["utc_time"], default=None)
            records.listings.append(
                Stock(item, qty, row["last_price"], row["retainer"], ts=row["first_seen_utc"],
                      undercut_count=row["undercut_count"],
                      competing_price=last["lowest_competitor"] if last else None,
                      observed_ts=last["utc_time"] if last else None)
            )
        elif outcome == "sold":
            ts = row["sale_time_utc"] or row["ended_utc"]
            require(ts is not None and row["sold_total_gil"] is not None)
            records.events.append(
                event(
                    SourceName.RESTOCKY, LedgerEventKind.SELL, item, qty, ts, row["sold_total_gil"]
                )
            )
            first = row["first_seen_utc"] if not row.get("history_only") else None
            if first is not None and (first > ts or not row["last_price"]):
                first = None
            rivals = row.get("rivals")
            if first is not None and not rivals:
                # The CSV spans listings. Only assign counts wholly inside this period.
                rivals = {r["competitor"]: r["times_below_us"] for r in records.competitors
                          if (r["item_id"], r["hq"]) == (item.id, item.hq)
                          and first <= r["first_seen_utc"] <= r["last_seen_utc"] <= ts}
                observed = Counter(
                    obs["seller"] for obs in records.undercuts
                    if (obs["item"], obs["hq"]) == (item.id, item.hq) and first <= obs["ts"] <= ts
                )
                for name, count in observed.items():
                    rivals.setdefault(name, count)
            cuts = None
            if first is not None and any(first <= r["utc_time"] <= ts for r in history):
                cuts = sum(
                    r["applied"] and r["lowest_competitor"] is not None
                    and r["new_price"] < min(r["old_price"], r["lowest_competitor"])
                    for r in history if first <= r["utc_time"] <= ts
                )
            records.events[-1].details = SaleDetails(
                retainer=row["retainer"], gross=row.get("sold_gross_gil"), first_listed=first,
                reprices=row["reprice_count"] if first is not None else None,
                own_undercuts=cuts, rivals={k: v for k, v in (rivals or {}).items() if v},
            )
        else:
            require(outcome == "gone")


def parse(paths: dict[str, Path]) -> Records:
    records = Records()
    for name, attr in (("competitors.csv", "competitors"), ("price-history.csv", "price_history")):
        if name in paths:
            setattr(records, attr, list(table(paths[name])))
    if "undercut-history.json" in paths:
        for obs in objects(paths["undercut-history.json"]):
            require(isinstance(obs["Seller"], str) and bool(obs["Seller"].strip()))
            require(uint(obs["OurPrice"]) > uint(obs["RivalPrice"]) > 0)
            records.undercuts.append(dict(item=uint(obs["ItemId"]), hq=flag(obs["IsHq"]),
                                          seller=obs["Seller"], ts=utc(obs["ObservedUtc"])))
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
    return records


def read(folder: Path) -> Source:
    return read_source(
        folder,
        SourceName.RESTOCKY,
        "Restocky",
        (*HEADERS, "listings.json", "cost-ledger.json", "undercut-history.json"),
        parse,
    )


def low_stock(root: Path) -> LowStockItems:
    """Mirror Restocky's offline calculation across the saved retainer snapshots."""
    try:
        config = json.loads((root / "Restocky.json").read_text(encoding="utf-8-sig"))
        watched = config["LowStock"]
        snapshots = config["Snapshots"]
        require(isinstance(watched, list) and isinstance(snapshots, dict))
        if not watched:
            return LowStockItems(reason="Restocky has no low-stock watch entries.")
        retainer_keys = {key for key, snapshot in snapshots.items() if isinstance(snapshot, dict)}
        if not retainer_keys:
            return LowStockItems(reason="Restocky has no saved retainer snapshots.")
        counts: Counter[tuple[int, bool]] = Counter()
        for row in objects(root / "Restocky" / "listings.json"):
            if row["Outcome"] == "active" and row["RetainerKey"] in retainer_keys:
                counts[uint(row["ItemId"]), flag(row["IsHq"])] += uint(row["Quantity"])
        entries = {}
        for row in watched:
            item_id, hq = uint(row["ItemId"]), flag(row["IsHq"])
            target, threshold = uint(row.get("Target", 3)), uint(row.get("Threshold", 1))
            listed = counts[item_id, hq]
            if item_id and listed <= threshold:
                entries[item_id, hq] = SearchEntry(
                    item_id=item_id, hq_only=hq, quantity=max(0, target - listed) or None,
                )
    except READ_ERRORS:
        return LowStockItems(reason="Restocky low-stock data is missing or unreadable.")
    return LowStockItems(
        entries=list(entries.values()),
        reason=None if entries else "No watched items are at or below their threshold.",
    )
