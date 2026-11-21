"""Per-host HTTP budget shared by desktop and CLI under APPDATA/Gilhound/http.

An OS byte lock holds each active connection slot and is released on process
exit. A separate short-lived lock serializes admission and cooldown changes.
The timestamp is atomically replaced so a crashed writer leaves valid state.
Only a hash of the hostname and its next permitted start time are persisted.
"""

import errno
import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from time import time as wall_time
from typing import BinaryIO

from gilhound.market.workers import checkpoint, wait

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def try_lock(path: Path) -> BinaryIO | None:
    handle = path.open("a+b")
    handle.seek(0)
    try:
        if os.name == "nt":
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            return None
        raise
    return handle


class HostBudget:
    def __init__(self, host: str, root: Path | None = None) -> None:
        if root is None:
            root = Path(os.environ.get("APPDATA", Path.home())) / "Gilhound" / "http"
        self.path = root / hashlib.sha256(host.lower().encode()).hexdigest()
        self.path.mkdir(parents=True, exist_ok=True)
        self.deadline = self.path / "next"

    @contextmanager
    def _gate(self) -> Iterator[None]:
        while (handle := try_lock(self.path / "gate")) is None:
            wait(0.01)
        with handle:
            yield

    def _next(self) -> float:
        try:
            return float(self.deadline.read_text())
        except FileNotFoundError:
            return 0

    def _save(self, deadline: float) -> None:
        temp = self.path / "pending"
        temp.write_text(str(deadline))
        temp.replace(self.deadline)

    def cooldown(self, seconds: float) -> None:
        with self._gate():
            self._save(max(self._next(), wall_time() + seconds))

    @contextmanager
    def connection(self) -> Iterator[None]:
        while True:
            checkpoint()
            slot = None
            with self._gate():
                delay = self._next() - wall_time()
                if delay <= 0:
                    for index in range(8):
                        slot = try_lock(self.path / str(index))
                        if slot:
                            break
                    if slot:
                        try:
                            self._save(wall_time() + 1 / 25)
                        except BaseException:
                            slot.close()
                            raise
            if slot:
                with slot:
                    yield
                return
            wait(min(60, max(delay, 0.01)))
