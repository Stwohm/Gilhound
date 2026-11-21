"""Capture and replay JSON requests made by the legacy urllib scripts."""

import hashlib
import io
import json
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Self


class UnknownRequest(LookupError):
    """A request was absent from the replay index."""


def request_key(method: str, url: str, body: bytes = b"") -> str:
    return hashlib.sha1(method.encode() + url.encode() + body).hexdigest()


def write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )


class Session:
    def __init__(
        self,
        mode: Literal["record", "replay"],
        directory: Path,
        transform: Callable[[str, Any], Any] | None,
    ) -> None:
        self.mode = mode
        self.directory = directory
        self.transform = transform
        self.original_urlopen = urllib.request.urlopen
        self.original_time = time.time
        self.original_sleep = time.sleep
        self.lock = threading.Lock()
        self.unanswered: set[str] = set()
        self.used: set[str] = set()
        self.closed = False
        if mode == "record":
            directory.mkdir(parents=True, exist_ok=True)
            if any(directory.iterdir()):
                raise ValueError("Recording directory must be empty")
            self.index = {"timestamp": time.time(), "requests": {}}
        elif mode == "replay":
            self.index = json.loads(
                (directory / "index.json").read_text(encoding="utf-8")
            )
        else:
            raise ValueError("Expected record or replay mode")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self.closed:
            return
        urllib.request.urlopen = self.original_urlopen
        time.time = self.original_time
        time.sleep = self.original_sleep
        self.closed = True
        if self.mode == "record":
            self.index["requests"] = dict(sorted(self.index["requests"].items()))
            write_json(self.directory / "index.json", self.index)

    def now(self) -> float:
        return self.index["timestamp"]

    def urlopen(
        self,
        url: str | urllib.request.Request,
        data: bytes | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> io.BytesIO:
        req = (
            url
            if isinstance(url, urllib.request.Request)
            else urllib.request.Request(url)
        )
        if data is not None:
            req.data = data
        method, full_url = req.get_method(), req.full_url
        key = request_key(method, full_url, req.data or b"")
        path = self.directory / f"{key}.json"
        with self.lock:
            if key in self.index["requests"]:
                self.used.add(key)
                return io.BytesIO(path.read_bytes())
            self.unanswered.add(key)
        if self.mode == "replay":
            raise UnknownRequest(f"No recorded response for {method} request {key}")
        with self.original_urlopen(req, *args, **kwargs) as response:
            payload = json.load(response)
        if self.transform is not None:
            payload = self.transform(full_url, payload)
        with self.lock:
            if key not in self.index["requests"]:
                write_json(path, payload)
                self.index["requests"][key] = {"method": method, "url": full_url}
            self.unanswered.discard(key)
            self.used.add(key)
            return io.BytesIO(path.read_bytes())


def install(
    mode: Literal["record", "replay"],
    dir: str | Path,
    *,
    transform: Callable[[str, Any], Any] | None = None,
) -> Session:
    """Install immediately. Use as a context manager or call close() to restore."""
    session = Session(mode, Path(dir), transform)
    urllib.request.urlopen = session.urlopen
    time.time = session.now
    if mode == "replay":
        # Legacy retry handlers catch unknown requests. Avoid sleeping in those handlers.
        time.sleep = lambda seconds: None
    return session
