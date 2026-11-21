import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from gilhound.runtime.bridge import Bridge, Heartbeat, Reply


def heartbeat(directory: Path, **changes: object) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1, "plugin_version": "0.3.9.0", "ts": int(time.time()),
        "logged_in": True, "world": "Cerberus", "enabled": True,
    }
    (directory / "heartbeat.json").write_text(json.dumps(payload | changes))


@pytest.mark.parametrize("ts", [1_700_000_000, 1_700_000_000.25])
def test_heartbeat_timestamp(tmp_path: Path, ts: int | float) -> None:
    heartbeat(tmp_path, ts=ts)
    beat = Heartbeat.model_validate_json((tmp_path / "heartbeat.json").read_bytes(), strict=True)
    assert beat.ts == ts


@pytest.mark.parametrize("ts", [1_700_000_000, 1_700_000_000.25])
def test_reply_timestamp(ts: int | float) -> None:
    reply = Reply.model_validate_json(json.dumps({
        "version": 1, "id": "test", "status": "rejected", "reason": "invalid command", "ts": ts,
    }), strict=True)
    assert reply.ts == ts


@pytest.mark.parametrize(("changes", "state"), [
    ({"enabled": False}, "disabled"),
    ({"ts": 1, "features": ["travel", "mb_search_name"]}, "not_running"),
    ({"version": 2}, "incompatible"), ({"plugin_version": "0.3.8.9"}, "incompatible"),
    ({"plugin_version": "0.3.10"}, "available"), ({"logged_in": False}, "available"),
])
def test_status(tmp_path: Path, changes: dict, state: str) -> None:
    bridge = Bridge(tmp_path)
    assert bridge.status().status == "not_running"
    heartbeat(bridge.directory, **changes)
    status = bridge.status()
    assert status.status == state
    if state == "not_running":
        assert not status.mb_search_name
    if state == "not_running" or changes.get("logged_in") is False:
        assert status.world is None
    if state != "available":
        assert bridge.send("/li Raiden market").status == "unavailable"
        assert not (bridge.directory / "inbox").exists()


@pytest.mark.parametrize(
    ("features", "mb_search_name", "include_item", "status", "reason"), [
        (["travel", "mb_search_name"], True, True, "ok", ""),
        (["travel", "mb_search"], True, True, "ok", ""),
        (["travel"], False, True, "ok", ""),
        (None, False, True, "ok", ""),
        (None, False, False, "ok", ""),
        (None, False, False, "rejected", "In combat"),
        (None, False, False, "error", "Lifestream unavailable"),
    ],
)
def test_round_trip(
    tmp_path: Path, client: TestClient, features: list[str] | None, mb_search_name: bool,
    include_item: bool, status: str, reason: str,
) -> None:
    directory = tmp_path / "plugins" / "Restocky" / "bridge"
    if features is None:
        heartbeat(directory)
    else:
        heartbeat(directory, plugin_version="0.3.10.0", features=features)
    (directory / "outbox").mkdir()
    assert client.get("/api/bridge").json() == {
        "status": "available", "world": "Cerberus",
        "plugin_version": "0.3.9.0" if features is None else "0.3.10.0",
        "mb_search_name": mb_search_name,
    }
    item = {"id": 123, "hq": True, "name": "Test Item"}
    payload = {"world": "Raiden", "item": item} if include_item else {"world": "Raiden"}

    def respond() -> None:
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            for path in (directory / "inbox").glob("*.json"):
                request = json.loads(path.read_bytes())
                assert request["id"] == path.stem and UUID(path.stem).version == 4
                assert request["version"] == 1 and request["command"] == "/li Raiden market"
                assert 0 <= time.time() - request["created_ts"] < 5
                if include_item:
                    assert request["item"] == item
                else:
                    assert "item" not in request
                (directory / "outbox" / path.name).write_text(json.dumps({
                    "version": 1, "id": path.stem, "status": status, "reason": reason,
                    "ts": int(time.time()) + 0.5,
                }))
                path.unlink()
                return
            time.sleep(0.01)
        pytest.fail("Missing inbox request")

    with ThreadPoolExecutor(max_workers=1) as worker:
        responder = worker.submit(respond)
        result = client.post("/api/bridge/go", json=payload)
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
