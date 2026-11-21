"""CashFlow SQLite V2.

ShopPurchaseSqlDescriptor.GetCsvExport and TabShopPurchases.DrawTable use
Price as a pre-tax *unit* price. Paid = stack subtotal + floor(subtotal/20),
except mannequin purchases, which have no surcharge. The anonymised fixture
contains repeated multi-unit purchases with an unchanged unit Price. In
contrast RetainerSales.Price is already net gil for the entire stack.
https://github.com/NightmareXIV/CashFlow/blob/main/CashFlow/Data/SqlDescriptors/ShopPurchaseSqlDescriptor.cs
See docs/research/taxes.md for the matched sale evidence.
"""

import sqlite3
from contextlib import closing
from io import BytesIO
from pathlib import Path

from gilhound.models import ItemRef, LedgerEventKind, SourceName

from .common import Records, Source, event, item_ref, read_source, require, uint

CORE = {
    "ShopPurchases": "Cid RetainerName Item Price Quantity IsMannequinn UnixTime",
    "RetainerSales": "Cid RetainerName Item Price Quantity IsMannequinn UnixTime Buyer",
    "GilRecords": "Cid GilPlayer GilRetainer UnixTime",
}
EXTRA = {
    "Trades": "Cid Self UnixTime Gil Items",
    "CidMap": "Cid Name HomeWorld",
    "NpcSales": "Cid Item Price Quantity UnixTime",
    "NpcPurchases": "Cid Item Price Quantity IsBuyback UnixTime",
}


def item(raw: int) -> ItemRef:
    require(0 < raw < 2_000_000)
    return item_ref(raw % 1_000_000, raw >= 1_000_000)


def trade_items(blob: bytes) -> list[tuple[int, int]]:
    """MessagePack ItemWithQuantity[]: arrays of [unsigned item ID, signed delta]."""
    require(isinstance(blob, bytes))
    stream = BytesIO(blob)

    def number(width: int, signed: bool = False) -> int:
        data = stream.read(width)
        require(len(data) == width)
        return int.from_bytes(data, "big", signed=signed)

    def array() -> int:
        tag = number(1)
        if 0x90 <= tag <= 0x9F:
            return tag - 0x90
        require(tag in (0xDC, 0xDD))
        return number(2 if tag == 0xDC else 4)

    def integer() -> int:
        tag = number(1)
        if tag <= 0x7F or tag >= 0xE0:
            return tag if tag < 128 else tag - 256
        require(0xCC <= tag <= 0xD3)
        return number(1 << ((tag - 0xCC) % 4), tag >= 0xD0)

    pairs = []
    for _ in range(array()):
        require(array() == 2)
        pairs.append((integer(), integer()))
    require(not stream.read(1))
    return pairs


def parse(paths: dict[str, Path]) -> Records:
    path = paths.get("DefaultDbV2.sqlite") or paths["DefaultDbV2.db"]
    records = Records()
    with (
        closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as live,
        closing(sqlite3.connect("")) as copy,
    ):
        live.backup(copy)
        tables = {r[0] for r in copy.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        require(CORE.keys() <= tables)
        for table, columns in (CORE | EXTRA).items():
            if table in tables:
                schema = {row[1] for row in copy.execute(f"PRAGMA table_info({table})")}
                require(set(columns.split()) <= schema)
        for table, kind in (
            ("ShopPurchases", LedgerEventKind.BUY),
            ("RetainerSales", LedgerEventKind.SELL),
        ):
            rows = copy.execute(
                f"SELECT Item, Quantity, Price, UnixTime, IsMannequinn FROM {table}"
            )
            for raw_item, qty, price, millis, mannequin in rows:
                raw_item, qty, price, millis, mannequin = map(
                    uint, (raw_item, qty, price, millis, mannequin)
                )
                require(mannequin in (0, 1))
                total = price
                if kind == LedgerEventKind.BUY:
                    gross = price * qty
                    total = gross if mannequin else gross + gross // 20
                records.events.append(
                    event(SourceName.CASHFLOW, kind, item(raw_item), qty, millis // 1000, total)
                )
        for cid, player, retainer, millis in copy.execute(
            "SELECT Cid, GilPlayer, GilRetainer, UnixTime FROM GilRecords"
        ):
            records.balances.append((uint(millis) // 1000, int(cid), uint(player), uint(retainer)))
        if "CidMap" in tables:
            for cid, name, world in copy.execute("SELECT Cid, Name, HomeWorld FROM CidMap"):
                require(isinstance(name, str))
                records.characters[int(cid)] = (name, uint(world))
        if "Trades" in tables:
            for millis, blob in copy.execute(
                "SELECT UnixTime, Items FROM Trades WHERE Items IS NOT NULL"
            ):
                for raw_item, qty in trade_items(blob):
                    if qty > 0:
                        records.events.append(
                            event(
                                SourceName.CASHFLOW,
                                LedgerEventKind.TRADE_IN,
                                item(raw_item),
                                qty,
                                uint(millis) // 1000,
                                None,
                            )
                        )
    return records


def read(folder: Path) -> Source:
    return read_source(
        folder,
        SourceName.CASHFLOW,
        "CashFlow",
        ("DefaultDbV2.sqlite", "DefaultDbV2.db", "DefaultDbV2.sqlite-wal", "DefaultDbV2.db-wal"),
        parse,
    )
