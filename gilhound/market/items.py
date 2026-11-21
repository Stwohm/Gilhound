"""English item names and market links shared by all display surfaces."""

import json
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPException
from itertools import batched
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from urllib.error import HTTPError
from urllib.parse import urlencode

from gilhound.market.http import HttpClient
from gilhound.market.universalis import Universalis
from gilhound.models import ItemRef

SHEET = "https://v2.xivapi.com/api/sheet/Item"
UNAVAILABLE = (OSError, HTTPException, ValueError, LookupError, TypeError)


def save_cache(path: Path, data: dict[int, str] | list[int]) -> None:
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def market_url(item_id: int, world: str) -> str:
    return f"https://universalis.app/market/{item_id}?" + urlencode({"server": world})


class ItemCatalog:
    def __init__(self, root: Path, http: HttpClient | None = None) -> None:
        self.icons: dict[int, str | None] = {}
        self.path = root / "items.json"
        self.http = http or HttpClient(timeout=4, attempts=1)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.names = {int(k): v for k, v in raw.items() if isinstance(v, str) and v}
        except (OSError, ValueError, TypeError, AttributeError):
            self.names = {}
        self.market_path = root / "marketable.json"
        try:
            self.marketable_ids = set(map(int, json.loads(self.market_path.read_text())))
            age = max(0.0, time.time() - self.market_path.stat().st_mtime)
            self.refresh_at = time.monotonic() + max(0.0, 86400 - age)
        except UNAVAILABLE:
            self.marketable_ids = set()
            self.refresh_at = 0.0
        self.market_lock = Lock()
        self.lock = Lock()
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.pending: set[int] = set()

    def decorate_icons(self, items: list[ItemRef]) -> None:
        for group in batched(sorted({item.id for item in items} - self.icons.keys()), 100):
            rows = ",".join(map(str, group))
            try:
                result = self.http.request(f"{SHEET}?rows={rows}&fields=Icon")
                for row in result["rows"]:
                    icon = row["fields"].get("Icon")
                    if not isinstance(icon, dict):
                        continue
                    path = icon.get("path_hr1") or icon.get("path")
                    if isinstance(path, str) and path:
                        query = urlencode({"path": path, "format": "png"})
                        self.icons[row["row_id"]] = f"https://v2.xivapi.com/api/asset?{query}"
                    else:
                        self.icons[row["row_id"]] = None
            except UNAVAILABLE:
                continue
        for item in items:
            item.icon_url = self.icons.get(item.id)

    def close(self) -> None:
        self.worker.shutdown(cancel_futures=True)

    def marketable(self) -> set[int]:
        with self.market_lock:
            if time.monotonic() >= self.refresh_at:
                try:
                    ids = set(Universalis(self.http).marketable())
                except UNAVAILABLE:
                    self.refresh_at = time.monotonic() + 30
                    if not self.marketable_ids:
                        raise
                else:
                    with self.lock:
                        self.marketable_ids = ids
                    self.refresh_at = time.monotonic() + 86400
                    try:
                        save_cache(self.market_path, sorted(ids))
                    except OSError:
                        pass  # The in-memory catalog is still usable.
            if not self.marketable_ids:
                raise ValueError("Marketable item list unavailable")
            return self.marketable_ids

    def fetch(self, ids: tuple[int, ...]) -> dict[int, str]:
        rows = ",".join(map(str, ids))
        try:
            result = self.http.request(f"{SHEET}?rows={rows}&fields=Name")
            records = result["rows"]
        except HTTPError as exc:
            if exc.code not in (400, 404, 422):
                raise
            records = []
        found = {r["row_id"]: r["fields"]["Name"] for r in records if r["fields"].get("Name")}
        for iid in ids:
            if iid not in found:
                try:
                    name = self.http.request(f"{SHEET}/{iid}?fields=Name")["fields"]["Name"]
                except HTTPError as exc:
                    if exc.code == 404:
                        continue
                    raise
                if name:
                    found[iid] = name
        return found

    def refresh(self, ids: set[int]) -> None:
        try:
            with self.lock:
                missing = sorted(ids - self.names.keys())
            for group in batched(missing, 100):
                found = self.fetch(group)
                if found:
                    with self.lock:
                        self.names.update(found)
                        snapshot = dict(self.names)
                    save_cache(self.path, snapshot)
        except UNAVAILABLE:
            pass  # Keep unresolved IDs eligible for the next refresh.
        try:
            self.marketable()
        except UNAVAILABLE:
            pass
        finally:
            with self.lock:
                self.pending.difference_update(ids)

    def decorate(self, items: Iterable[ItemRef], world: str, *, wait: bool = False) -> None:
        refs = list(items)
        if not refs:
            return
        ids = {item.id for item in refs}
        with self.lock:
            needed = ids - self.names.keys()
            if time.monotonic() >= self.refresh_at:
                needed = ids
            needed -= self.pending
            if needed:
                self.pending.update(needed)
                self.worker.submit(self.refresh, needed)
            # A barrier also waits for an earlier background lookup of the same IDs.
            barrier = self.worker.submit(lambda: None) if wait else None
        if barrier:
            barrier.result()
        with self.lock:
            for item in refs:
                fallback = f"Item {item.id}" if item.name == str(item.id) else item.name
                item.name = self.names.get(item.id, fallback)
                item.universalis_url = market_url(item.id, world)
                item.saddlebag_url = (
                    f"https://saddlebagexchange.com/queries/item-data/{item.id}"
                    if item.id in self.marketable_ids
                    else None
                )
