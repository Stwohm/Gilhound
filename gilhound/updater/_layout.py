"""Shared on-disk protocol for the launcher and updater (stdlib only)."""

import os
import re
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

EXE = "gilhound-app.exe"
_NUMBER = r"(0|[1-9][0-9]*)"
_IDS = r"[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*"
_VERSION = re.compile(rf"{_NUMBER}\.{_NUMBER}\.{_NUMBER}(?:-({_IDS}))?(?:\+{_IDS})?")


def precedence(version: str) -> tuple:
    match = _VERSION.fullmatch(version)
    if not match:
        raise ValueError("Invalid release version")
    major, minor, patch, pre = match.groups()
    identifiers = []
    for part in pre.split(".") if pre else []:
        if part.isdigit() and len(part) > 1 and part.startswith("0"):
            raise ValueError("Invalid numeric prerelease identifier")
        identifiers.append((0, int(part)) if part.isdigit() else (1, part))
    return int(major), int(minor), int(patch), pre is None, tuple(identifiers)


def install_root() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Gilhound"


def pointer(root: Path, name: str) -> str | None:
    try:
        value = (root / name).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    precedence(value)
    return value


def write_pointer(root: Path, name: str, version: str) -> None:
    precedence(version)
    with NamedTemporaryFile(dir=root, delete=False) as temp:
        path = Path(temp.name)
        temp.write(version.encode("ascii"))
    try:
        path.replace(root / name)
    finally:
        path.unlink(missing_ok=True)


def ready(folder: Path) -> bool:
    return (folder / "READY").is_file() and (folder / EXE).is_file()


@contextmanager
def lock(path: Path, *, shared: bool = False, timeout: float = 30) -> Iterator[None]:
    """Process-owned byte lock. Windows and POSIX release it on process exit."""
    with path.open("a+b") as handle:
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt

            def acquire() -> None:
                mode = msvcrt.LK_NBRLCK if shared else msvcrt.LK_NBLCK
                msvcrt.locking(handle.fileno(), mode, 1)
        else:
            import fcntl

            def acquire() -> None:
                mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
                fcntl.flock(handle, mode | fcntl.LOCK_NB)

        deadline = time.monotonic() + timeout
        while True:
            try:
                acquire()
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Installation is busy") from None
                time.sleep(0.05)
        yield

