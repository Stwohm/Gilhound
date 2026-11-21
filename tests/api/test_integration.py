import time
from collections.abc import Callable
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient

from gilhound import __version__
from gilhound.api.app import create_app
from gilhound.ingest.common import Records, Source, Stock, event, item_ref
from gilhound.models import (
    LedgerEventKind,
    RetainerScanParams,
    RetainerScanResult,
    ScanParams,
    ScanResult,
    SourceName,
    SourceStatus,
)
from gilhound.runtime import Runtime
from gilhound.sources.fixtures import install


def test_local_integration(tmp_path: Path, token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = Runtime(tmp_path / "state", tmp_path / "empty")
    ready, release = Event(), Event()

    def retainer(
        params: RetainerScanParams, progress: Callable[[int, int], None] | None = None,
        profile: str | None = None,
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
    icons = ("favicon.ico", "favicon.png", "apple-touch-icon.png")
    for name in icons:
        (tmp_path / name).write_bytes(name.encode())
    app = create_app(token, runtime, dist=tmp_path, schedule=False)
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/", headers={"Host": "foreign.invalid"}).status_code == 400
        assert token in client.get("/").text
        for name in icons:
            assert client.get(f"/{name}").content == name.encode()
            assert client.get(f"/{name}", headers={"Host": "foreign.invalid"}).status_code == 400
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


def test_position_views_and_cost(
    tmp_path: Path, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = Runtime(tmp_path, tmp_path / "empty")
    listed, sold = item_ref(5056, False), item_ref(5333, True)
    records = Records(
        listings=[Stock(listed, 3)],
        events=[
            event(SourceName.CASHFLOW, LedgerEventKind.BUY, sold, 1, 1, 6),
            event(SourceName.CASHFLOW, LedgerEventKind.SELL, sold, 1, 2, 9),
        ],
    )
    source = Source(
        SourceStatus(name=SourceName.ALLAGAN_MARKET, found=True, recognised=True), records
    )
    monkeypatch.setattr("gilhound.runtime.read_plugins", lambda _: [source])
    fixtures = Path(__file__).resolve().parents[1] / "fixtures" / "market"
    with install("replay", fixtures / "items"):
        runtime.items.decorate([listed, sold], "Cerberus", wait=True)
        with TestClient(
            create_app(token, runtime, schedule=False), base_url="http://localhost"
        ) as client:
            client.headers.update({"Authorization": f"Bearer {token}"})
            (position,) = client.get("/api/positions").json()
            assert position["item"]["name"] == "Bronze Ingot" and position["avg_cost"] is None
            (closed,) = client.get("/api/positions?view=closed").json()
            assert closed["item"]["name"] == "Hempen Yarn" and closed["realised_profit"] == 3
            assert (
                client.put(
                    "/api/positions/cost",
                    json={
                        "item_id": 5056,
                        "hq": False,
                        "unit_cost": 7,
                    },
                ).status_code
                == 204
            )
            (updated,) = client.get("/api/positions").json()
            assert updated["avg_cost"] == 7 and updated["known_capital"] == 21
            change = {"item_id": listed.id, "hq": listed.hq, "personal": True}
            assert client.put("/api/positions/personal", json=change).status_code == 204
            assert client.get("/api/positions").json() == []
            assert client.get("/api/positions?view=closed").json() == [closed]
            change["personal"] = False
            assert client.put("/api/positions/personal", json=change).status_code == 204
            assert client.get("/api/positions").json() == [updated]


def test_live_snipe_controls(tmp_path: Path, token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = Runtime(tmp_path)
    entered, release = Event(), Event()

    def scan(params: ScanParams, settings: object, profile: str) -> tuple[ScanResult, list]:
        entered.set()
        assert release.wait(3)
        return ScanResult(params=params, rows=[], started_ts=1, finished_ts=2), []

    monkeypatch.setattr(runtime, "_snipe_scan", scan)
    with TestClient(
        create_app(token, runtime),
        base_url="http://localhost",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        settings = client.get("/api/settings").json()

        def save(**changes: object) -> None:
            settings.update(changes)
            assert client.put("/api/settings", json=settings).is_success

        def status() -> dict:
            return client.get("/api/snipes/status").json()

        assert settings["auto_snipe_enabled"]
        save(scan_interval_min=1)
        assert 58 <= status()["next_run_ts"] - time.time() <= 60
        deadline = status()["next_run_ts"]
        save(discord_notifier=True)
        assert status()["next_run_ts"] == deadline
        save(auto_snipe_enabled=False)
        assert status()["next_run_ts"] is None
        save(auto_snipe_enabled=True)
        assert entered.wait(2)
        save(auto_snipe_enabled=False)
        assert status()["running"]
        release.set()
        for _ in range(100):
            if not status()["running"]:
                break
            time.sleep(0.01)
        result = status()
        assert result["last_run_ts"] and not result["running"]
        assert result["fresh"] == result["stale"] == result["notified"] == 0
        assert result["next_run_ts"] is None
        assert client.post("/api/snipes/run").json() == []
        assert (
            client.put("/api/settings", json={**settings, "scan_interval_min": 241}).status_code
            == 422
        )
