"""Local settings and SQLite history shared by the desktop and standalone CLI."""

import json
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock

from pydantic import ValidationError

from gilhound.models import ScanResult, SnipeHit, UserSettings


def data_dir() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "Gilhound"


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = RLock()

    def get(self) -> UserSettings:
        with self.lock:
            try:
                return UserSettings.model_validate_json(self.path.read_bytes())
            except FileNotFoundError:
                return UserSettings(home_world="Cerberus", buy_region="Europe")
            except ValidationError:
                raise ValueError(
                    "Gilhound settings could not be read. Check settings.json."
                ) from None

    def save(self, settings: UserSettings) -> UserSettings:
        with self.lock:
            secret = settings.webhook_url
            if "webhook_url" not in settings.model_fields_set or (
                secret and secret.get_secret_value() == "**********"
            ):
                secret = self.get().webhook_url
            values = settings.model_dump()
            values["webhook_url"] = secret.get_secret_value() if secret else None
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(dir=self.path.parent, delete=False) as temporary:
                tmp = Path(temporary.name)
            try:
                tmp.write_text(json.dumps(values), encoding="utf-8")
                tmp.replace(self.path)
            finally:
                tmp.unlink(missing_ok=True)
            return settings.model_copy(update={"webhook_url": secret})


class History:
    def __init__(self, path: Path, limit: int = 20) -> None:
        self.path = path
        self.limit = limit
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS scans (id INTEGER PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS current_hits (key TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS hits (
                    key TEXT PRIMARY KEY, body TEXT NOT NULL, first_seen INTEGER NOT NULL,
                    last_seen INTEGER NOT NULL, notified INTEGER, lease_until REAL);
            """)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=15)) as db, db:
            yield db

    def save_scan(self, result: ScanResult) -> None:
        with self.connection() as db:
            db.execute("INSERT INTO scans(body) VALUES (?)", (result.model_dump_json(),))
            db.execute(
                "DELETE FROM scans WHERE id NOT IN (SELECT id FROM scans ORDER BY id DESC LIMIT ?)",
                (self.limit,),
            )

    def scans(self) -> list[ScanResult]:
        with self.connection() as db:
            return [
                ScanResult.model_validate_json(body)
                for (body,) in db.execute("SELECT body FROM scans ORDER BY id DESC")
            ]

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
