import re
from pathlib import Path

from gilhound.ingest.restocky import low_stock
from gilhound.sources.fixtures import write_json
from tools.anonymize import Anonymizer


def test_read_low_stock_from_watch_and_listing_snapshots(tmp_path: Path) -> None:
    assert low_stock(tmp_path).reason
    folder = tmp_path / "Restocky"
    folder.mkdir()
    anonymizer = Anonymizer(b"synthetic-low-stock-fixture", [re.compile(r"synthetic-[a-z]+")])
    config = {
        "LowStock": [
            {"ItemId": 101, "IsHq": True, "Threshold": 2, "Target": 7},
            {"ItemId": 102, "IsHq": False, "Threshold": 1, "Target": 5},
            {"ItemId": 103, "IsHq": False},
        ],
        "Snapshots": {"$type": "Dictionary", "synthetic-retainer": {}},
    }
    listing = {"Outcome": "active", "RetainerKey": "synthetic-retainer", "Quantity": 2}
    rows = [
        {**listing, "ItemId": 101, "IsHq": True},
        {**listing, "ItemId": 101, "IsHq": False, "Quantity": 50},
        {**listing, "ItemId": 101, "IsHq": True, "Outcome": "sold"},
        {**listing, "ItemId": 102, "IsHq": False},
        {**listing, "ItemId": 103, "IsHq": False, "RetainerKey": "synthetic-unknown"},
    ]
    write_json(tmp_path / "Restocky.json", anonymizer.market(config))
    write_json(folder / "listings.json", anonymizer.market(rows))
    result = low_stock(tmp_path)
    assert result.reason is None
    assert [(row.item_id, row.hq_only, row.quantity) for row in result.entries] == [
        (101, True, 5), (103, False, 3),
    ]
    (folder / "listings.json").write_text("invalid JSON", encoding="utf-8")
    assert low_stock(tmp_path).entries == [] and low_stock(tmp_path).reason
