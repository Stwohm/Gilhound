"""Local partner share format and anonymous, per-configuration sweep history."""

import base64
import hashlib
import json
import re
import time
import zlib
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import Future
from datetime import UTC, datetime
from threading import Lock

from gilhound.market.http import HttpClient
from gilhound.market.retainer import batches
from gilhound.market.universalis import Universalis
from gilhound.market.workers import Workers
from gilhound.models import PartnerShare, RetainerItemRow, ScanRow, UserSettings
from gilhound.runtime.store import History

MAX_CODE = 256_000
MAX_JSON = 512_000


def encode(share: PartnerShare) -> str:
    data = share.model_dump_json(exclude_defaults=True).encode()
    code = base64.urlsafe_b64encode(zlib.compress(data, 9)).decode().rstrip("=")
    if len(data) > MAX_JSON or len(code) + 4 > MAX_CODE:
        raise ValueError("Share exceeds size limit")
    return "GH1." + code


def decode(code: str) -> PartnerShare:
    if len(code) > MAX_CODE or not re.fullmatch(r"GH1\.[A-Za-z0-9_-]+", code):
        raise ValueError("Invalid share code")
    try:
        raw = base64.urlsafe_b64decode(code[4:] + "=" * (-len(code[4:]) % 4))
        stream = zlib.decompressobj()
        data = stream.decompress(raw, MAX_JSON + 1)
        if len(data) > MAX_JSON or not stream.eof or stream.unused_data:
            raise ValueError
        return PartnerShare.model_validate_json(data, strict=True)
    except (ValueError, zlib.error) as exc:
        raise ValueError("Invalid share code") from exc


def targets(settings: UserSettings) -> dict[str, set[str]]:
    groups = defaultdict(set)
    for entry in settings.partner_retainers:
        groups[(entry.world.strip() or settings.home_world).casefold()].add(
            entry.name.strip().casefold()
        )
    return groups


def scope(settings: UserSettings) -> str:
    key = sorted((w, sorted(names)) for w, names in targets(settings).items())
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()


type Stocks = dict[tuple[int, bool], tuple[int, int]]


def collect(settings: UserSettings) -> Stocks:
    api = Universalis(HttpClient())
    worlds = {w.id: w.name for dc in api.data_centers() for w in dc.worlds}
    canonical = {name.casefold(): name for name in worlds.values()}
    ids = api.marketable()
    stock: Stocks = {}
    for world, names in targets(settings).items():
        if world not in canonical:
            raise ValueError("Unknown partner world")
        for _, snapshots in batches(api, canonical[world], ids, worlds):
            for iid, item in snapshots.items():
                for row in item.listings:
                    if (row.retainer_name or "").casefold() in names:
                        key = (iid, row.hq)
                        qty, seen = stock.get(key, (0, 0))
                        stock[key] = qty + row.qty, max(seen, row.last_review_ts)
    return stock


class Partners:
    def __init__(self, history: History, settings: Callable[[], UserSettings]) -> None:
        self.history = history
        self.settings = settings
        self.pool = Workers(1)
        self.lock = Lock()
        self.pending: Future | None = None
        self.pending_scope = ""
        with history.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS partner_sweep(
                    id INTEGER PRIMARY KEY, scope TEXT, ts INTEGER);
                CREATE INDEX IF NOT EXISTS partner_scope ON partner_sweep(scope,id);
                CREATE TABLE IF NOT EXISTS partner_stock(
                    sweep INTEGER, item INTEGER, hq INTEGER, qty INTEGER, seen INTEGER,
                    PRIMARY KEY(sweep,item,hq));
            """)

    def record(self, settings: UserSettings, stock: Stocks, ts: int) -> None:
        with self.history.connection() as db:
            db.execute(
                "DELETE FROM partner_stock WHERE sweep IN "
                "(SELECT id FROM partner_sweep WHERE ts < ?)",
                (ts - 91 * 86400,),
            )
            db.execute("DELETE FROM partner_sweep WHERE ts < ?", (ts - 91 * 86400,))
            key = db.execute(
                "INSERT INTO partner_sweep(scope,ts) VALUES (?,?)", (scope(settings), ts)
            ).lastrowid
            db.executemany(
                "INSERT INTO partner_stock VALUES (?,?,?,?,?)",
                ((key, iid, hq, qty, seen) for (iid, hq), (qty, seen) in stock.items()),
            )

    def status(self, settings: UserSettings) -> dict:
        with self.history.connection() as db:
            last = db.execute(
                "SELECT max(ts) FROM partner_sweep WHERE scope=?", (scope(settings),)
            ).fetchone()[0]
        age = None if last is None else max(0, int(time.time()) - last) // 60
        return {
            "retainers": len(settings.partner_retainers),
            "age_min": age,
            "stale": age is None or age >= settings.partner_cache_min,
        }

    def refresh(self, force: bool = False) -> Future | None:
        settings = self.settings()
        if not settings.partner_retainers:
            return None
        key = scope(settings)
        with self.lock:
            if self.pending and not self.pending.done() and self.pending_scope == key:
                return self.pending
            if not force and not self.status(settings)["stale"]:
                return None

            def sweep() -> None:
                self.record(settings, collect(settings), int(time.time()))

            self.pending_scope = key
            self.pending = self.pool.submit(sweep)
            return self.pending

    def mark(self, rows: list[ScanRow] | list[RetainerItemRow], settings: UserSettings) -> None:
        key = scope(settings)
        now = int(time.time())
        start = (now // 86400 - settings.partner_window_days + 1) * 86400
        with self.history.connection() as db:
            latest = db.execute(
                """
                SELECT item,hq,qty,seen FROM partner_stock
                WHERE sweep IN (SELECT id FROM partner_sweep WHERE scope=? ORDER BY id DESC LIMIT 4)
                ORDER BY sweep""",
                (key,),
            )
            recent = {(i, bool(h)): (q, t) for i, h, q, t in latest}
            regular = {
                (i, bool(h)): days
                for i, h, days in db.execute(
                    """
                SELECT item,hq,count(DISTINCT ts/86400) FROM partner_stock
                JOIN partner_sweep ON id=sweep WHERE scope=? AND ts>=?
                GROUP BY item,hq HAVING count(DISTINCT ts/86400)>=?""",
                    (key, start, settings.partner_regular_days),
                )
            }
        share = settings.partner_manual
        manual = {(i.id, i.hq) for i in share.items} if share else set()
        for row in rows:
            item = row.item.id, row.item.hq
            row.partner = row.partner_detail = None
            if item in recent:
                qty, seen = recent[item]
                row.partner = "now"
                hours = max(0, now - seen) // 3600
                row.partner_detail = f"Partner listing now: {qty} units, seen {hours} h ago"
            elif item in regular:
                row.partner = "regular"
                row.partner_detail = (
                    f"Partner regular: seen {regular[item]} of {settings.partner_window_days} days"
                )
            elif share and item in manual:
                row.partner = "manual"
                date = datetime.fromtimestamp(share.created_ts, UTC).date()
                row.partner_detail = f"Partner asked to avoid (shared {date})"
