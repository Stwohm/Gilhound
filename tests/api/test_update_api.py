import json
import time
from io import BytesIO
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from gilhound import __version__, updater
from gilhound.api import updates
from gilhound.api.jobs import Progress


def test_dev_skips_feed(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    check = Mock()
    monkeypatch.setattr(updater, "check", check)
    info = client.get("/api/info").json()
    assert info["latest_version"] is None
    assert not info["updates_supported"]
    response = client.post("/api/update/check")
    assert response.status_code == 409
    assert response.json()["detail"] == "Updates unavailable in dev mode."
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


def test_manual_check_bypasses_cache_and_rate_limits(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(updates, "installed", lambda: True)
    clock = Mock(wraps=time)
    clock.monotonic.return_value = 100.0
    monkeypatch.setattr(updates, "time", clock)
    major, minor, _ = map(int, __version__.split("."))
    newer = f"{major}.{minor + 1}.0"
    feed = Mock(side_effect=[
        BytesIO(f'{{"tag_name": "v{__version__}"}}'.encode()),
        BytesIO(json.dumps({"tag_name": f"v{newer}", "assets": [
            {"name": f"Gilhound-{newer}-win64.zip", "browser_download_url": "asset"},
            {"name": "SHA256SUMS.txt", "browser_download_url": "sum"},
        ]}).encode()),
    ])
    monkeypatch.setattr(updater, "_open", feed)

    response = client.post("/api/update/check")
    assert response.status_code == 200
    info = response.json()
    assert info["version"] == __version__
    assert info["update_available"] is False
    assert info["latest_version"] is None
    assert isinstance(info["update_checked_at"], int)
    assert client.get("/api/info").json() == info

    clock.monotonic.return_value = 109.0
    response = client.post("/api/update/check")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "1"
    feed.assert_called_once()

    clock.monotonic.return_value = 110.0
    response = client.post("/api/update/check")
    assert response.status_code == 200
    assert response.json()["update_available"] is True
    assert response.json()["latest_version"] == newer
    assert client.get("/api/info").json() == response.json()
    assert feed.call_count == 2


def test_manual_check_reports_feed_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(updates, "installed", lambda: True)
    feed = Mock(side_effect=OSError("Internal feed error"))
    monkeypatch.setattr(updater, "_open", feed)

    response = client.post("/api/update/check")
    assert response.status_code == 502
    message = response.json()["detail"]
    assert message == (
        "Could not check for updates. The release feed is unavailable. Please try again."
    )
    info = client.get("/api/info").json()
    assert info["update_check_error"] == message
    assert isinstance(info["update_checked_at"], int)
    assert not info["update_available"]
    assert client.post("/api/update/check").status_code == 429
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


@pytest.mark.parametrize("restart", [False, True])
def test_update_job_explains_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, restart: bool,
) -> None:
    release = updater.UpdateInfo("9.0.0", "asset", "sum")
    monkeypatch.setattr(updates, "installed", lambda: True)
    monkeypatch.setattr(updater, "check", lambda: release)
    failure = PermissionError("access denied")
    stage = Mock(side_effect=None if restart else failure)
    monkeypatch.setattr(updater, "download_and_stage", stage)
    monkeypatch.setattr(updater, "apply_and_restart", Mock(side_effect=failure))
    assert client.post("/api/update/check").status_code == 200
    job = client.post("/api/update/apply").json()
    deadline = time.monotonic() + 5
    while job["state"] in {"queued", "running"} and time.monotonic() < deadline:
        job = client.get("/api/jobs/" + job["id"]).json()
    assert job["state"] == "failed"
    phase = "restart the installed launcher" if restart else "download and stage the update"
    assert phase in job["error"] and "PermissionError" in job["error"]
