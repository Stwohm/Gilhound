"""Read item IDs from GatherBuddy Reborn's auto-gather lists."""

import json
from pathlib import Path


def read(directory: Path) -> frozenset[int]:
    try:
        lists = json.loads((directory / "auto_gather_lists.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, RecursionError):
        return frozenset()
    if not isinstance(lists, list):
        return frozenset()
    return frozenset(
        item_id
        for entry in lists
        if isinstance(entry, dict) and isinstance(ids := entry.get("ItemIds"), list)
        for item_id in ids
        if type(item_id) is int and item_id > 0
    )
