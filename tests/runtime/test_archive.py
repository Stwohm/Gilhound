import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest

from gilhound.models import RetainerScanParams, ScanParams, ScanResult
from gilhound.runtime.store import History


def test_migration_retention_and_lookup(tmp_path: Path) -> None:
    path = tmp_path / "history.db"
    now = int(time.time())
    params = ScanParams(home_world="Home", buy_region="Region", seller_tax=0.05)
    result = ScanResult(params=params, rows=[], started_ts=now - 2, finished_ts=now)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE scans(id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
        db.execute("INSERT INTO scans(body) VALUES (?)", (result.model_dump_json(),))
    history = History(path)
    for _ in range(21):
        history.save_scan(result, "Example")
    assert len(History(path).entries()) == 22
    entries = history.entries("scan", "Example", now - 2, now, limit=5)
    assert len(entries) == 5
    entry = history.entry(entries[0].id)
    assert entry.duration_s == 2 and entry.result == result and entry.description
    assert not history.entries(search="missing")
    assert not history.entries(until=now - 2)
    assert history.delete(entry.id)
    with pytest.raises(KeyError):
        history.entry(entry.id)


def test_failed_retainer_run_preserves_snapshot(tmp_path: Path) -> None:
    history = History(tmp_path / "history.db")
    params = RetainerScanParams(retainer_name="Synthetic", world="Home", buy_region="Region")
    with pytest.raises(TimeoutError), history.attempt(params, "Watch"):
        raise TimeoutError("private error text")
    (summary,) = history.entries("retainer")
    result = history.entry(summary.id).result
    assert result.params == params and result.warnings == ["Scan failed (TimeoutError)."]
    history.save_retainer_profile("Saved", params)
    assert history.retainer_profiles() == {"Saved": params}
    with pytest.raises(ValueError):
        history.save_retainer_profile("saved", params)
