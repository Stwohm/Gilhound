"""Allagan Tools InventoryItem CSV, including the retainer market container."""

from pathlib import Path

from gilhound.models import SourceName

from .common import Records, Source, Stock, csv_rows, item_ref, read_source, require, uint

# InventoryTools uses CriticalCommonLib InventoryItem.ToCsv: column 20 is
# SortedContainer. Virtual storage can have raw Container (column 0) = Bag0.
# https://github.com/Critical-Impact/CriticalCommonLib/blob/main/Models/InventoryItem.cs
# Equipped (1000/11000), armoury (3200–3209, 3300, 3400, 3500), armoire
# (2500) and glamour dresser (2501) are outside the sellable whitelist.
# https://github.com/Critical-Impact/CriticalCommonLib/blob/main/Enums/InventoryType.cs
# Bag0–3, SaddleBag0–1, PremiumSaddleBag0–1, RetainerBag0–6.
SELLABLE = frozenset((*range(4), 4000, 4001, 4100, 4101, *range(10000, 10007)))
RETAINER_MARKET = 12002


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
            item_id, qty = nums[2:4]
            container = nums[20]
            price = uint(row[24])
            if item_id in (0, 1) or qty == 0:  # empty slots and gil balances
                continue
            require(qty > 0)
            stock = Stock(
                item_ref(item_id, bool(nums[6] & 1)), qty, price, str(nums[23]), container, ts
            )
            result.inventory.append(stock)
            if container == RETAINER_MARKET:
                result.listings.append(stock)
        return result

    return read_source(
        folder, SourceName.ALLAGAN_TOOLS, "Allagan Tools", ("inventories.csv",), parse
    )
