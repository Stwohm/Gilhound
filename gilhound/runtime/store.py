"""Local settings and SQLite history shared by the desktop and standalone CLI."""

import json
import os
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from pydantic import BaseModel

from gilhound.market.budget import try_lock
from gilhound.models import (
    RetainerScanParams,
    RetainerScanResult,
    ScanParams,
    ScanResult,
    SnipeHit,
    SnipeStatus,
    UserSettings,
)

from .profiles import Profile, describe

type Kind = Literal["scan", "snipe", "retainer"]


class ArchiveSummary(BaseModel):
    id: int
    kind: Kind
    profile: str | None
    started_ts: int
    duration_s: float
    row_count: int


class ArchiveEntry(ArchiveSummary):
    description: list[str]
    result: ScanResult | RetainerScanResult


def data_dir() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "Gilhound"


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while (handle := try_lock(self.path.with_suffix(".lock"))) is None:
            time.sleep(0.01)
        with handle:
            yield

    def get(self) -> UserSettings:
        with self._locked():
            return self._read()

    def _read(self) -> UserSettings:
        try:
            values = json.loads(self.path.read_bytes())
            # Older releases allowed intervals above the current input range.
            if isinstance(values, dict) and isinstance(values.get("scan_interval_min"), int):
                values["scan_interval_min"] = min(values["scan_interval_min"], 240)
            return UserSettings.model_validate(values)
        except FileNotFoundError:
            return UserSettings(home_world="Cerberus", buy_region="Europe")
        except ValueError:
            raise ValueError("Gilhound settings could not be read. Check settings.json.") from None

    def save(self, settings: UserSettings) -> UserSettings:
        fields = dict(settings)
        for field in ("webhook_url", "column_order", "search_mode"):
            if field not in settings.model_fields_set:
                fields.pop(field)
        return self.update(fields)[1]

    def update(
        self, fields: dict, *, column_order: tuple[str, list[str]] | None = None,
    ) -> tuple[UserSettings, UserSettings]:
        with self._locked():
            previous = self._read()
            if column_order is not None:
                key, order = column_order
                orders = previous.column_order.copy()
                if order:
                    orders[key] = order
                else:
                    orders.pop(key, None)
                fields = {**fields, "column_order": orders}
            settings = UserSettings.model_validate({**dict(previous), **fields}, extra="forbid")
            secret = settings.webhook_url
            if secret and secret.get_secret_value() == "**********":
                secret = previous.webhook_url
            values = settings.model_dump()
            values["webhook_url"] = secret.get_secret_value() if secret else None
            with NamedTemporaryFile(dir=self.path.parent, delete=False) as temporary:
                tmp = Path(temporary.name)
            try:
                tmp.write_text(json.dumps(values), encoding="utf-8")
                tmp.replace(self.path)
            finally:
                tmp.unlink(missing_ok=True)
            return previous, settings.model_copy(update={"webhook_url": secret})


class History:
    def __init__(self, path: Path, retention: Callable[[], int] = lambda: 90) -> None:
        self.path = path
        self.retention = retention
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS last_snipe_run (id INTEGER PRIMARY KEY, body TEXT);
                CREATE TABLE IF NOT EXISTS scans (id INTEGER PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS current_hits (key TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS hits (
                    key TEXT PRIMARY KEY, body TEXT NOT NULL, first_seen INTEGER NOT NULL,
                    last_seen INTEGER NOT NULL, notified INTEGER, lease_until REAL);
            """)

            db.execute("""CREATE TABLE IF NOT EXISTS archive (
                id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, profile TEXT, started_ts INTEGER,
                duration_s REAL, row_count INTEGER, body TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS archive_time ON archive(started_ts)")
            db.execute("""CREATE TABLE IF NOT EXISTS retainer_profiles (
                name TEXT PRIMARY KEY COLLATE NOCASE, params TEXT NOT NULL)""")
            db.execute("BEGIN IMMEDIATE")
            db.execute("""INSERT INTO archive(kind,started_ts,duration_s,row_count,body)
                SELECT CASE json_extract(body,'$.params.preset') WHEN 'snipe' THEN 'snipe'
                    ELSE 'scan' END, json_extract(body,'$.started_ts'),
                    json_extract(body,'$.finished_ts')-json_extract(body,'$.started_ts'),
                    json_array_length(body,'$.rows'),body FROM scans ORDER BY id""")
            db.execute("DELETE FROM scans")
            self._prune(db)

    def _prune(self, db: sqlite3.Connection) -> None:
        db.execute(
            "DELETE FROM archive WHERE started_ts < ?",
            (int(time.time()) - self.retention() * 86400,),
        )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=15)) as db, db:
            yield db

    def last_run(self) -> SnipeStatus:
        with self.connection() as db:
            row = db.execute("SELECT body FROM last_snipe_run WHERE id=1").fetchone()
            return SnipeStatus.model_validate_json(row[0]) if row else SnipeStatus()

    def save_run(self, status: SnipeStatus) -> None:
        with self.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO last_snipe_run VALUES (1,?)", (status.model_dump_json(),)
            )

    @contextmanager
    def attempt(
        self, params: ScanParams | RetainerScanParams, profile: str | None
    ) -> Iterator[int]:
        started = int(time.time())
        try:
            yield started
        except Exception as exc:
            finished = int(time.time())
            warnings = [f"Scan failed ({type(exc).__name__})."]
            if isinstance(params, RetainerScanParams):
                result = RetainerScanResult(
                    params=params,
                    rows=[],
                    items_scanned=0,
                    duration_s=max(0, finished - started),
                    warnings=warnings,
                )
            else:
                result = ScanResult(
                    params=params,
                    rows=[],
                    started_ts=started,
                    finished_ts=finished,
                    warnings=warnings,
                )
            self.save_scan(result, profile, started=started)
            raise

    def save_scan(
        self,
        result: ScanResult | RetainerScanResult,
        profile: str | None = None,
        *,
        started: int | None = None,
    ) -> None:
        if isinstance(result, ScanResult):
            kind = "snipe" if result.params.preset == "snipe" else "scan"
            started = result.started_ts
            duration = result.finished_ts - started
        else:
            kind = "retainer"
            duration = result.duration_s
            if started is None:
                started = int(time.time() - duration)
        with self.connection() as db:
            db.execute(
                """INSERT INTO archive(kind,profile,started_ts,duration_s,row_count,body)
                          VALUES (?,?,?,?,?,?)""",
                (
                    kind,
                    profile,
                    started,
                    duration,
                    len(result.rows),
                    result.model_dump_json(serialize_as_any=True),
                ),
            )
            self._prune(db)

    def entries(
        self,
        kind: Kind | None = None,
        profile: str | None = None,
        since: int | None = None,
        until: int | None = None,
        search: str = "",
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ArchiveSummary]:
        rows, _ = self.entry_page(kind, profile, since, until, search, limit, offset)
        return rows

    def entry_page(
        self,
        kind: Kind | None = None,
        profile: str | None = None,
        since: int | None = None,
        until: int | None = None,
        search: str = "",
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[ArchiveSummary], int]:
        query = " FROM archive WHERE 1"
        args = []
        for clause, value in (
            ("kind=?", kind),
            ("profile=?", profile),
            ("started_ts>=?", since),
            ("started_ts<?", until),
        ):
            if value is not None:
                query += " AND " + clause
                args.append(value)
        if search:
            query += """ AND EXISTS (
                SELECT 1 FROM (SELECT value FROM json_each(body,'$.rows')
                               UNION ALL SELECT value FROM json_each(body,'$.rejected'))
                WHERE instr(lower(json_extract(value,'$.item.name')),lower(?))>0)"""
            args.append(search)
        page = ("SELECT id,kind,profile,started_ts,duration_s,row_count" + query
                + " ORDER BY started_ts DESC,id DESC LIMIT ? OFFSET ?")
        with self.connection() as db:
            self._prune(db)
            db.row_factory = sqlite3.Row
            rows = [
                ArchiveSummary(**row)
                for row in db.execute(page, [*args, limit if limit is not None else -1, offset])
            ]
            if limit is None and offset == 0:
                return rows, len(rows)
            total = db.execute("SELECT count(*)" + query, args).fetchone()[0]
            return rows, total

    def entry(self, key: int) -> ArchiveEntry:
        with self.connection() as db:
            self._prune(db)
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM archive WHERE id=?", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        if row["kind"] == "retainer":
            result = RetainerScanResult.model_validate_json(row["body"])
            p = result.params
            description = [f"Compare {p.retainer_name} on {p.world} with {p.buy_region}."]
        else:
            result = ScanResult.model_validate_json(row["body"])
            description = describe(Profile(params=result.params))
        return ArchiveEntry(**row, result=result, description=description)

    def retainer_profiles(self) -> dict[str, RetainerScanParams]:
        with self.connection() as db:
            return {
                name: RetainerScanParams.model_validate_json(params)
                for name, params in db.execute("SELECT name,params FROM retainer_profiles")
            }

    def save_retainer_profile(self, name: str, params: RetainerScanParams) -> None:
        if not name.strip() or len(name) > 80:
            raise ValueError("Choose a profile name up to 80 characters.")
        with self.connection() as db:
            try:
                db.execute(
                    "INSERT INTO retainer_profiles VALUES (?,?)",
                    (name.strip(), params.model_dump_json()),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Choose a unique profile name.") from exc

    def delete(self, key: int) -> bool:
        with self.connection() as db:
            return bool(db.execute("DELETE FROM archive WHERE id=?", (key,)).rowcount)

    def record_hits(self, hits: list[SnipeHit]) -> None:
        """Replace the current feed atomically while preserving earlier sightings."""
        with self.connection() as db:
            db.execute("DELETE FROM current_hits")
            db.executemany(
                "INSERT OR IGNORE INTO current_hits VALUES (?)", [(h.listing_key,) for h in hits]
            )
            db.executemany(
                """INSERT INTO hits(key,body,first_seen,last_seen) VALUES (?,?,?,?)
                       ON CONFLICT(key) DO UPDATE SET
                           body=excluded.body, last_seen=excluded.last_seen
                       WHERE excluded.last_seen >= hits.last_seen""",
                [
                    (hit.listing_key, hit.model_dump_json(), hit.first_seen_ts, hit.first_seen_ts)
                    for hit in hits
                ],
            )

    def hits(self, *, current: bool = False) -> list[SnipeHit]:
        query = "SELECT body,first_seen,notified FROM hits"
        if current:
            query += " WHERE key IN (SELECT key FROM current_hits)"
        with self.connection() as db:
            return [
                SnipeHit.model_validate_json(body).model_copy(
                    update={"first_seen_ts": first, "notified_ts": notified}
                )
                for body, first, notified in db.execute(query + " ORDER BY last_seen DESC, rowid")
            ]

    def reserve(self, key: str, cooldown: float) -> float | None:
        now = time.time()
        lease = now + 120
        with self.connection() as db:
            updated = db.execute(
                """UPDATE hits SET lease_until=? WHERE key=?
                AND (lease_until IS NULL OR lease_until<=?)
                AND (notified IS NULL OR notified<=?)""",
                (lease, key, now, int(now) - cooldown),
            )
            return lease if updated.rowcount else None

    def release(self, key: str, lease: float, delivered: bool) -> None:
        with self.connection() as db:
            db.execute(
                """UPDATE hits SET lease_until=NULL, notified=COALESCE(?, notified)
                          WHERE key=? AND lease_until=?""",
                (int(time.time()) if delivered else None, key, lease),
            )

    def extend(self, key: str, lease: float, delay: float) -> float:
        until = time.time() + delay + 120
        with self.connection() as db:
            updated = db.execute(
                "UPDATE hits SET lease_until=? WHERE key=? AND lease_until=?", (until, key, lease)
            )
            if not updated.rowcount:
                raise RuntimeError("Notification reservation expired.")
        return until
