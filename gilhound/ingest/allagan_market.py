"""AllaganMarket/Models/{SaleItem,SoldItem}.cs ToCsv, Critical-Impact/AllaganMarket.

Naive invariant-culture DateTime.Now uses the recording PC's timezone. Pass its
tzinfo when importing elsewhere. Without an override Python uses OS local time
at the event date. SoldItems records listing prices; fallback net is estimated.
"""

from datetime import datetime, tzinfo
from pathlib import Path

from gilhound.models import LedgerEventKind, SourceName

from .common import Records, Source, Stock, csv_rows, event, item_ref, read_source, require, uint


def read(folder: Path, local_tz: tzinfo | None = None, seller_tax_pct: int = 5) -> Source:
    def timestamp(raw: str) -> int:
        return int(datetime.strptime(raw, "%m/%d/%Y %H:%M:%S").replace(tzinfo=local_tz).timestamp())

    def parse(paths: dict[str, Path]) -> Records:
        records = Records()
        for filename, path in paths.items():
            sold = filename == "SoldItems.csv"
            if not sold:
                records.listings = []
                records.listings_mtime = int(path.stat().st_mtime)
            for row in csv_rows(path):
                require(len(row) == 8 if sold else len(row) in (9, 10))
                owner, _, item_id, quality, qty, price = map(uint, row[:6])
                require(quality in (0, 1))
                ts = timestamp(row[7])
                if sold:
                    rate = uint(row[6]) or seller_tax_pct
                    require(0 <= rate <= 100)
                else:
                    updated = timestamp(row[8])
                    if len(row) == 10:
                        uint(row[9])
                if item_id == 0 and qty == 0 and not sold:
                    continue
                item = item_ref(item_id, bool(quality))
                require(qty > 0)
                if sold:
                    gross = price * qty
                    records.events.append(
                        event(
                            SourceName.ALLAGAN_MARKET,
                            LedgerEventKind.SELL,
                            item,
                            qty,
                            ts,
                            gross - gross * rate // 100,
                        )
                    )
                else:
                    records.listings.append(Stock(item, qty, price, str(owner), ts=updated))
        return records

    return read_source(
        folder,
        SourceName.ALLAGAN_MARKET,
        "AllaganMarket",
        ("SaleItems.csv", "SoldItems.csv"),
        parse,
    )
