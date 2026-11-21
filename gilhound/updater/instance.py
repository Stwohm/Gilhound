"""Desktop ownership and focus requests for one installed directory."""

import ctypes
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

from ._layout import EXE, precedence, same_location

if os.name == "nt":
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateEventW.restype = wintypes.HANDLE
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.SetEvent.argtypes = [wintypes.HANDLE]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    user = ctypes.windll.user32
    user.IsIconic.argtypes = [wintypes.HWND]
    user.SetForegroundWindow.argtypes = [wintypes.HWND]
    user.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]


def _name(root: Path, kind: str) -> str:
    identity = root.stat()
    return rf"Local\Gilhound-{kind}-{identity.st_dev:x}-{identity.st_ino:x}"


@contextmanager
def owner_mutex(root: Path) -> Iterator[bool]:
    """The desktop keeps the sole persistent handle until its window closes."""
    handle = kernel.CreateMutexW(None, False, _name(root, "owner"))
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
    finally:
        kernel.CloseHandle(handle)


@contextmanager
def show_event(root: Path) -> Iterator[int]:
    handle = kernel.CreateEventW(None, False, False, _name(root, "show"))
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield handle
    finally:
        kernel.CloseHandle(handle)


def _image(pid: int) -> Path | None:
    if os.name != "nt" or not 0 < pid <= 0xffffffff:
        return None
    handle = kernel.OpenProcess(0x101000, False, pid)  # QUERY_LIMITED_INFORMATION | SYNCHRONIZE
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if (kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))
                and kernel.WaitForSingleObject(handle, 0) == 258):
            return Path(buffer.value)
        return None
    finally:
        kernel.CloseHandle(handle)


def window_pid(root: Path) -> int | None:
    """Caller holds window.lock across validation and focus or registration."""
    path = root / "window.pid"
    try:
        pid = int(path.read_text(encoding="ascii"))
        image = _image(pid)
        if (image and image.name.lower() == EXE and image.parent.name.startswith("app-")
                and same_location(image.parent.parent, root)):
            version = precedence(image.parent.name[4:])
            if version < precedence("0.2.4"):  # Older desktops only publish a PID.
                return pid
            with owner_mutex(root) as first:
                if not first:
                    return pid
    except FileNotFoundError:
        return None
    except ValueError:
        pass
    path.unlink(missing_ok=True)
    logging.info("stale instance file removed")
    return None


def request_show(pid: int, handle: int) -> None:
    user.AllowSetForegroundWindow(pid)
    if not kernel.SetEvent(handle):
        raise ctypes.WinError(ctypes.get_last_error())
    logging.info("focused existing pid %d", pid)


def wait_show(handle: int) -> bool:
    status = kernel.WaitForSingleObject(handle, 200)
    if status == 258:  # WAIT_TIMEOUT
        return False
    if status != 0:
        raise ctypes.WinError(ctypes.get_last_error())
    return True


def foreground(hwnd: int) -> None:
    user.SetForegroundWindow(hwnd)


def minimized(hwnd: int) -> bool:
    return bool(user.IsIconic(hwnd))
