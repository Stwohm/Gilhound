import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from gilhound.runtime.bridge import Bridge


def heartbeat(directory: Path, **changes: object) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1, "plugin_version": "0.3.9.0", "ts": int(time.time()),
        "logged_in": True, "world": "Cerberus", "enabled": True,
    }
    (directory / "heartbeat.json").write_text(json.dumps(payload | changes))


@pytest.mark.parametrize(("changes", "state"), [
    ({"enabled": False}, "disabled"), ({"ts": 1}, "not_running"),
    ({"version": 2}, "incompatible"), ({"plugin_version": "0.3.8.9"}, "incompatible"),
    ({"plugin_version": "0.3.10"}, "available"), ({"logged_in": False}, "available"),
])
def test_status(tmp_path: Path, changes: dict, state: str) -> None:
    bridge = Bridge(tmp_path)
    assert bridge.status().status == "not_running"
    heartbeat(bridge.directory, **changes)
    status = bridge.status()
    assert status.status == state
    if state == "not_running" or changes.get("logged_in") is False:
        assert status.world is None
    if state != "available":
        assert bridge.send("/li Raiden market").status == "unavailable"
        assert not (bridge.directory / "inbox").exists()


@pytest.mark.parametrize(("status", "reason"), [
    ("ok", ""), ("rejected", "In combat"), ("error", "Lifestream unavailable"),
])
def test_round_trip(tmp_path: Path, client: TestClient, status: str, reason: str) -> None:
    directory = tmp_path / "plugins" / "Restocky" / "bridge"
    heartbeat(directory)
    (directory / "outbox").mkdir()
    assert client.get("/api/bridge").json() == {
        "status": "available", "plugin_version": "0.3.9.0", "world": "Cerberus",
    }

    def respond() -> None:
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            for path in (directory / "inbox").glob("*.json"):
                request = json.loads(path.read_bytes())
                assert request["id"] == path.stem and UUID(path.stem).version == 4
                assert request["version"] == 1 and request["command"] == "/li Raiden market"
                assert 0 <= time.time() - request["created_ts"] < 5
                (directory / "outbox" / path.name).write_text(json.dumps({
                    "version": 1, "id": path.stem, "status": status, "reason": reason,
                    "ts": int(time.time()),
                }))
                path.unlink()
                return
            time.sleep(0.01)
        pytest.fail("Missing inbox request")

    with ThreadPoolExecutor(max_workers=1) as worker:
        responder = worker.submit(respond)
        result = client.post("/api/bridge/go", json={"world": "Raiden"})
        responder.result()
    assert result.json() == {"status": status, "reason": reason}
    retry = client.post("/api/bridge/go", json={"world": "Raiden"}).json()
    assert retry["status"] == "rejected" and "two seconds" in retry["reason"]
    assert not list((directory / "inbox").iterdir())


def test_invalid_command_and_timeout(tmp_path: Path) -> None:
    bridge = Bridge(tmp_path)
    heartbeat(bridge.directory)
    for command in ["/li A market", "/li Raiden market\n", "/li Raiden\n/logout market"]:
        assert bridge.send(command).status == "rejected"
    assert not (bridge.directory / "inbox").exists()
    assert bridge.send("/li Alpha-Beta's World market").status == "error"
