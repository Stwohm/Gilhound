"""File-based travel requests to the Restocky plugin."""

import json
import re
import time
from pathlib import Path
from threading import Lock
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from gilhound.ingest.discover import plugin_config_root
from gilhound.models import BridgeItem, BridgeResult, BridgeStatus

COMMAND = re.compile(r"/li [A-Za-z][A-Za-z' -]{1,30} market")


class Heartbeat(BaseModel):
    version: int
    plugin_version: str
    ts: float
    logged_in: bool
    world: str | None
    enabled: bool
    features: list[str] = Field(default_factory=list)


class Reply(BaseModel):
    version: Literal[1]
    id: str
    status: Literal["ok", "rejected", "error"]
    reason: str
    ts: float


class Bridge:
    def __init__(self, plugins: Path | None = None) -> None:
        self.directory = (plugins or plugin_config_root()) / "Restocky" / "bridge"
        self._lock = Lock()
        self._last_send = float("-inf")

    def status(self) -> BridgeStatus:
        try:
            beat = Heartbeat.model_validate_json(
                (self.directory / "heartbeat.json").read_bytes(), strict=True
            )
        except (OSError, ValidationError):
            return BridgeStatus(status="not_running")
        state = BridgeStatus(
            status="available", plugin_version=beat.plugin_version,
            world=beat.world if beat.logged_in else None,
        )
        if not 0 <= time.time() - beat.ts < 15:
            state.status = "not_running"
            state.world = None
            return state
        state.mb_search_name = "mb_search_name" in beat.features or "mb_search" in beat.features
        if (
            beat.version != 1
            or not re.fullmatch(r"[0-9]{1,6}(\.[0-9]{1,6}){2,3}", beat.plugin_version)
            or tuple(map(int, beat.plugin_version.split("."))) < (0, 3, 9)
        ):
            state.status = "incompatible"
        elif not beat.enabled:
            state.status = "disabled"
        return state

    def send(self, command: str, item: BridgeItem | None = None) -> BridgeResult:
        if not COMMAND.fullmatch(command):
            return BridgeResult(status="rejected", reason="Invalid travel command.")
        with self._lock:
            state = self.status()
            if state.status != "available":
                reason = {
                    "not_running": "Restocky not running", "disabled": "Disabled in Restocky",
                    "incompatible": "Restocky 0.3.9+ required",
                }[state.status]
                return BridgeResult(status="unavailable", reason=reason)
            if time.monotonic() - self._last_send < 2:
                return BridgeResult(status="rejected", reason="Wait two seconds between commands.")
            request_id = str(uuid4())
            temporary = self.directory / "inbox" / f"{request_id}.tmp"
            request: dict[str, object] = {
                "version": 1, "id": request_id, "command": command,
                "created_ts": int(time.time()),
            }
            if item is not None:
                request["item"] = item.model_dump()
            try:
                temporary.parent.mkdir(exist_ok=True)
                temporary.write_text(json.dumps(request), encoding="utf-8")
                temporary.replace(temporary.with_suffix(".json"))
            except OSError:
                return BridgeResult(status="error", reason="Could not write the Restocky request.")
            self._last_send = time.monotonic()
        path = self.directory / "outbox" / f"{request_id}.json"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                reply = Reply.model_validate_json(path.read_bytes(), strict=True)
                if reply.id == request_id:
                    return BridgeResult(status=reply.status, reason=reply.reason)
            except (OSError, ValidationError):
                pass  # The responder may still be writing the file.
            time.sleep(0.05)
        return BridgeResult(status="error", reason="Restocky did not reply within five seconds.")
