"""One auto-reset show event per Windows session and installed directory."""

import ctypes
import os
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

if os.name == "nt":
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateEventW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.SetEvent.argtypes = [wintypes.HANDLE]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    user = ctypes.windll.user32
    user.IsIconic.argtypes = [wintypes.HWND]
    user.SetForegroundWindow.argtypes = [wintypes.HWND]
    user.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]


@contextmanager
def show_event(root: Path) -> Iterator[tuple[int, bool]]:
    if os.name != "nt":
        yield 0, True
        return
    identity = root.stat()
    name = rf"Local\Gilhound-show-{identity.st_dev:x}-{identity.st_ino:x}"
    handle = kernel.CreateEventW(None, False, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield handle, ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
    finally:
        kernel.CloseHandle(handle)


def request_show(root: Path, handle: int) -> None:
    try:
        pid = int((root / "window.pid").read_text())
        user.AllowSetForegroundWindow(pid)
    except (OSError, ValueError):
        pass  # The event also queues a request during startup.
    if not kernel.SetEvent(handle):
        raise ctypes.WinError(ctypes.get_last_error())


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
