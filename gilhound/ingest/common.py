"""Read-only plugin boundary types. Snapshots are observations of existing stock."""

import csv
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import Field

from gilhound.models import (
    ItemRef,
    LedgerEvent,
    LedgerEventKind,
    SaleDetails,
    SourceName,
    SourceStatus,
)

READ_ERRORS = (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, csv.Error)


class Transaction(LedgerEvent):
    """ppu is the integer unit amount. stack_gil preserves tax and division remainders."""

    details: SaleDetails = Field(default_factory=SaleDetails, exclude=True)
    sources: list[SourceName] = Field(default_factory=list, exclude=True)
    stack_gil: int | None = Field(default=None, ge=0)


@dataclass(frozen=True)
class Stock:
    item: ItemRef
    qty: int
    ppu: int | None = None
    owner: str = ""
    container: int | None = None
    ts: int = 0
    undercut_count: int | None = None
    competing_price: int | None = None
    observed_ts: int | None = None


@dataclass
class Records:
    events: list[Transaction] = field(default_factory=list)
    # None means no export. An empty list is an observed empty snapshot.
    listings: list[Stock] | None = None
    listings_mtime: int | None = None
    inventory: list[Stock] | None = None
    remaining_costs: list[Stock] = field(default_factory=list)
    # (epoch seconds, character ID, player gil, retainer gil)
    balances: list[tuple[int, int, int, int]] = field(default_factory=list)
    characters: dict[int, tuple[str, int]] = field(default_factory=dict)
    competitors: list[dict] = field(default_factory=list)
    price_history: list[dict] = field(default_factory=list)
    undercuts: list[dict] = field(default_factory=list)
    warning: str | None = None


@dataclass
class Source:
    status: SourceStatus
    records: Records


def require(condition: bool) -> None:
    if not condition:
        raise ValueError("Unexpected plugin schema")


def uint(value: object) -> int:
    require(type(value) in (int, str) and str(value).isascii() and str(value).isdigit())
    return int(str(value))


def item_ref(item_id: int, hq: bool, name: str = "") -> ItemRef:
    return ItemRef(id=item_id, hq=hq, name=name or f"Item {item_id}")


def event(
    source: SourceName, kind: LedgerEventKind, item: ItemRef, qty: int, ts: int, total: int | None
) -> Transaction:
    require(qty > 0)
    return Transaction(
        source=source,
        kind=kind,
        item=item,
        qty=qty,
        ts=ts,
        stack_gil=total,
        ppu=None if total is None else total // qty,
    )


def csv_rows(path: Path) -> Iterator[list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        yield from csv.reader(stream, strict=True)


def read_source(
    folder: Path,
    name: SourceName,
    label: str,
    filenames: tuple[str, ...],
    parser: Callable[[dict[str, Path]], Records],
) -> Source:
    status = SourceStatus(name=name, found=False, recognised=False)
    try:
        paths = {f: folder / f for f in filenames if (folder / f).is_file()}
        if not paths:
            status.warning = f"{label} source missing"
            return Source(status, Records())
        status.found = True
        status.last_update_ts = int(max(p.stat().st_mtime for p in paths.values()))
        records = parser(paths)
        status.recognised = True
        status.warning = records.warning
        return Source(status, records)
    except READ_ERRORS:
        status.warning = f"{label} format not recognised"
        return Source(status, Records())
