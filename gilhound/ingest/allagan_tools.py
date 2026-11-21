"""Allagan Tools InventoryItem CSV, including the retainer market container."""

from pathlib import Path

from gilhound.models import SourceName

from .common import Records, Source, Stock, csv_rows, item_ref, read_source, require, uint


def read(folder: Path) -> Source:
    def parse(paths: dict[str, Path]) -> Records:
        result = Records(inventory=[], listings=[])
        path = paths["inventories.csv"]
        ts = int(path.stat().st_mtime)
        result.listings_mtime = ts
        for row in csv_rows(path):
            require(len(row) == 27)
            nums = list(map(int, row[:25]))
            for gearset in filter(None, row[25].split(";")):
                uint(gearset)
            container, _, item_id, qty = nums[:4]
            price = uint(row[24])
            if item_id in (0, 1) or qty == 0:  # empty slots and gil balances
                continue
            require(qty > 0)
            stock = Stock(
                item_ref(item_id, bool(nums[6] & 1)), qty, price, str(nums[23]), container, ts
            )
            result.inventory.append(stock)
            if container == 12002:
                result.listings.append(stock)
        return result

    return read_source(
        folder, SourceName.ALLAGAN_TOOLS, "Allagan Tools", ("inventories.csv",), parse
    )
