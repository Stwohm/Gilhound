import time
from collections.abc import Callable
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient

from gilhound import __version__
from gilhound.api.app import create_app
from gilhound.models import RetainerScanParams, RetainerScanResult, ScanParams, ScanResult
from gilhound.runtime import Runtime


def test_local_integration(tmp_path: Path, token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = Runtime(tmp_path / "state", tmp_path / "empty")
    ready, release = Event(), Event()

    def retainer(
        params: RetainerScanParams, progress: Callable[[int, int], None] | None = None
    ) -> RetainerScanResult:
        if progress:
            progress(3, 8)
            ready.set()
            release.wait(2)
        return RetainerScanResult(params=params, rows=[], items_scanned=8, duration_s=1)

    def scan(params: ScanParams) -> ScanResult:
        return ScanResult(params=params, rows=[], started_ts=1, finished_ts=2)

    sent = []
    monkeypatch.setattr(runtime, "retainer", retainer)
    monkeypatch.setattr(runtime, "scan", scan)
    monkeypatch.setattr(runtime, "run_snipes", list)
    monkeypatch.setattr(runtime.discord, "test", sent.append)
    (tmp_path / "index.html").write_text("<html><head></head></html>")
    app = create_app(token, runtime, dist=tmp_path, schedule=False)
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/", headers={"Host": "foreign.invalid"}).status_code == 400
        assert token in client.get("/").text
        assert client.get("/api/settings").status_code == 401
        client.headers.update({"Authorization": f"Bearer {token}"})
        assert client.get("/api/info").json()["version"] == __version__
        assert (
            client.post(
                "/api/scan",
                json={"home_world": "World", "buy_region": "Region", "seller_tax": 0.05},
            ).json()["finished_ts"]
            == 2
        )
        assert (
            client.post("/api/retainer-scan", json={"retainer_name": "Synthetic"}).json()[
                "items_scanned"
            ]
            == 8
        )
        assert client.post("/api/snipes/run").json() == client.get("/api/snipes").json() == []
        settings = client.get("/api/settings").json()
        settings["home_world"] = "World"
        assert client.put("/api/settings", json=settings).json()["home_world"] == "World"
        assert client.post("/api/settings/discord/test").status_code == 204
        assert len(sent) == 1
        assert client.get("/api/positions").json() == []
        assert client.get("/api/pnl").json()["sales_proceeds"] == 0
        assert len(client.get("/api/sources").json()) == 4
        assert client.post("/api/diagnostics/export").content.startswith(b"PK")
        response = client.post("/api/jobs/retainer", json={"retainer_name": "Synthetic"})
        assert response.status_code == 202
        path = "/api/jobs/" + response.json()["id"]
        assert ready.wait(2)
        state = client.get(path).json()
        assert (state["state"], state["done"], state["total"]) == ("running", 3, 8)
        release.set()
        for _ in range(100):
            state = client.get(path).json()
            if state["state"] == "complete":
                break
            time.sleep(0.01)
        assert state["result"]["items_scanned"] == 8
        assert client.get("/api/jobs/unknown").status_code == 404
