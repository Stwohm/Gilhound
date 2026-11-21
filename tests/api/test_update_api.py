import time
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from gilhound import updater
from gilhound.api import updates
from gilhound.api.jobs import Progress


def test_dev_skips_feed(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    check = Mock()
    monkeypatch.setattr(updater, "check", check)
    assert client.get("/api/info").json()["latest_version"] is None
    assert client.post("/api/update/apply").status_code == 409
    check.assert_not_called()


def test_release_job(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    release = updater.UpdateInfo("0.2.0", "asset", "sum")
    checked, allow_check, staged, allow_restart = Event(), Event(), Event(), Event()
    restarted = Event()

    def check() -> updater.UpdateInfo:
        checked.set()
        assert allow_check.wait(5)
        return release

    def stage(info: updater.UpdateInfo, progress: Progress) -> None:
        assert info == release
        progress(5, 12)
        staged.set()
        assert allow_restart.wait(5)

    monkeypatch.setattr(updates, "installed", lambda: True)
    feed = Mock(side_effect=check)
    monkeypatch.setattr(updater, "check", feed)
    monkeypatch.setattr(updater, "download_and_stage", stage)
    monkeypatch.setattr(updater, "apply_and_restart", restarted.set)
    assert client.get("/api/info").json()["update_available"] is False
    assert checked.wait(2)
    allow_check.set()
    for _ in range(100):
        info = client.get("/api/info").json()
        if info["update_available"]:
            break
        time.sleep(0.01)
    assert info["latest_version"] == release.version
    response = client.post("/api/update/apply")
    assert response.status_code == 202
    assert staged.wait(2)
    job = client.get("/api/jobs/" + response.json()["id"]).json()
    assert (job["state"], job["done"], job["total"]) == ("running", 5, 12)
    assert client.post("/api/update/apply").status_code == 409
    allow_restart.set()
    assert restarted.wait(2)
    feed.assert_called_once()


def test_feed_failure_cached(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(updates, "installed", lambda: True)
    feed = Mock(side_effect=OSError)
    monkeypatch.setattr(updater, "check", feed)
    assert not client.get("/api/info").json()["update_available"]
    log = tmp_path / "state/logs/gilhound.log"
    for _ in range(100):
        if "Update check failed" in log.read_text():
            break
        time.sleep(0.01)
    assert "WARNING Update check failed" in log.read_text()
    assert client.get("/api/info").json()["latest_version"] is None
    feed.assert_called_once()
