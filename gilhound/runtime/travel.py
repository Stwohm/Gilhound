"""One explicit Lifestream command per request to the foreground game window."""

import ctypes as c
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from ctypes import wintypes as w
from pathlib import PureWindowsPath
from threading import Lock


def command(world: str) -> str:
    if not world.isalpha() or len(world) > 32:
        raise ValueError("Invalid world name.")
    return f"/li {world.lower()} market"


class Travel:
    def __init__(self) -> None:
        self.lock = Lock()
        self.next_click = 0.0

    def go(self, world: str, delay_ms: int, factory: Callable = lambda: Windows()) -> bool:
        line = command(world)
        if not self.lock.acquire(blocking=False):
            raise BlockingIOError("Travel is already in progress.")
        try:
            if time.monotonic() < self.next_click:
                raise BlockingIOError("Wait two seconds between travel commands.")
            self.next_click = time.monotonic() + 2
            native = factory()
            window = native.find_game()
            if not window:
                return False
            native.send(window, line, delay_ms / 1000)
            return True
        finally:
            self.lock.release()


class Keyboard(c.Structure):
    _fields_ = [
        ("vk", w.WORD),
        ("scan", w.WORD),
        ("flags", w.DWORD),
        ("time", w.DWORD),
        ("extra", c.c_size_t),
    ]


class Mouse(c.Structure):
    _fields_ = [
        ("x", w.LONG),
        ("y", w.LONG),
        ("data", w.DWORD),
        ("flags", w.DWORD),
        ("time", w.DWORD),
        ("extra", c.c_size_t),
    ]


class Payload(c.Union):
    _fields_ = [("keyboard", Keyboard), ("mouse", Mouse)]


class Input(c.Structure):
    _fields_ = [("type", w.DWORD), ("data", Payload)]


class Windows:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("Game travel requires Windows.")
        self.u, self.k, self.o = c.windll.user32, c.windll.kernel32, c.windll.ole32
        # Declare pointer-sized arguments explicitly on 64-bit Windows.
        h, d, p = w.HANDLE, w.DWORD, c.c_void_p
        self.callback = c.WINFUNCTYPE(w.BOOL, h, w.LPARAM)
        for lib, name, result, args in [
            (self.u, "EnumWindows", w.BOOL, [self.callback, w.LPARAM]),
            (self.u, "GetWindowThreadProcessId", d, [h, c.POINTER(d)]),
            (self.u, "IsWindowVisible", w.BOOL, [h]),
            (self.k, "OpenProcess", h, [d, w.BOOL, d]),
            (self.k, "QueryFullProcessImageNameW", w.BOOL, [h, d, w.LPWSTR, c.POINTER(d)]),
            (self.k, "CloseHandle", w.BOOL, [h]),
            (self.u, "GetForegroundWindow", h, []),
            (self.u, "SetForegroundWindow", w.BOOL, [h]),
            (self.u, "ShowWindow", w.BOOL, [h, c.c_int]),
            (self.u, "IsIconic", w.BOOL, [h]),
            (self.u, "OpenClipboard", w.BOOL, [h]),
            (self.u, "SetClipboardData", h, [w.UINT, h]),
            (self.k, "GlobalAlloc", h, [w.UINT, c.c_size_t]),
            (self.k, "GlobalLock", p, [h]),
            (self.k, "GlobalUnlock", w.BOOL, [h]),
            (self.k, "GlobalFree", h, [h]),
            (
                self.u,
                "CreateWindowExW",
                h,
                [d, w.LPCWSTR, w.LPCWSTR, d, c.c_int, c.c_int, c.c_int, c.c_int, h, h, h, p],
            ),
            (self.u, "DestroyWindow", w.BOOL, [h]),
            (self.u, "GetClipboardData", h, [w.UINT]),
            (self.o, "OleDuplicateData", h, [h, w.WORD, w.UINT]),
            (self.u, "SendInput", w.UINT, [w.UINT, c.POINTER(Input), c.c_int]),
        ]:
            fn = getattr(lib, name)
            fn.restype, fn.argtypes = result, args

    def find_game(self) -> int | None:
        found = None

        @self.callback
        def visit(hwnd: int, _: int) -> bool:
            nonlocal found
            pid = w.DWORD()
            self.u.GetWindowThreadProcessId(hwnd, c.byref(pid))
            process = self.k.OpenProcess(0x1000, False, pid)
            if process:
                try:
                    size = w.DWORD(32768)
                    path = c.create_unicode_buffer(size.value)
                    if self.k.QueryFullProcessImageNameW(process, 0, path, c.byref(size)):
                        if PureWindowsPath(
                            path.value
                        ).name.lower() == "ffxiv_dx11.exe" and self.u.IsWindowVisible(hwnd):
                            found = hwnd
                finally:
                    self.k.CloseHandle(process)
            return found is None

        self.u.EnumWindows(visit, 0)
        return found

    def focus(self, hwnd: int) -> None:
        foreground = self.u.GetForegroundWindow()
        current = self.k.GetCurrentThreadId()
        thread = self.u.GetWindowThreadProcessId(foreground, None)
        pid = w.DWORD()
        self.u.GetWindowThreadProcessId(hwnd, c.byref(pid))
        self.u.AllowSetForegroundWindow(pid)
        attached = thread != current and self.u.AttachThreadInput(current, thread, True)
        try:
            if self.u.IsIconic(hwnd):
                self.u.ShowWindow(hwnd, 9)
            self.u.SetForegroundWindow(hwnd)
        finally:
            if attached:
                self.u.AttachThreadInput(current, thread, False)

    @contextmanager
    def clipboard(self, line: str) -> Iterator[None]:
        # Materialize every clipboard format before replacing it. OleDuplicateData
        # handles both global memory and standard GDI clipboard handles.
        owner = self.u.CreateWindowExW(0, "STATIC", "", 0, 0, 0, 0, 0, None, None, None, None)
        if not owner:
            raise OSError("Could not create clipboard owner.")
        saved = {}
        sequence = None
        try:
            if not self.u.OpenClipboard(owner):
                raise OSError("Clipboard is busy.")
            try:
                fmt = self.u.EnumClipboardFormats(0)
                while fmt:
                    if fmt in (0x80, 3, 0x83) or 0x200 <= fmt < 0x400:
                        raise OSError("Current clipboard format cannot be preserved.")
                    handle = self.u.GetClipboardData(fmt)
                    if not handle:
                        raise OSError("Could not read the previous clipboard.")
                    if fmt in (14, 0x8E):
                        copy_emf = c.windll.gdi32.CopyEnhMetaFileW
                        copy_emf.restype, copy_emf.argtypes = w.HANDLE, [w.HANDLE, w.LPCWSTR]
                        duplicate = copy_emf(handle, None)
                    else:
                        duplicate = self.o.OleDuplicateData(handle, 2 if fmt == 0x82 else fmt, 0)
                    if not duplicate:
                        raise OSError("Could not preserve clipboard.")
                    saved[fmt] = duplicate
                    fmt = self.u.EnumClipboardFormats(fmt)
                data = (line + "\0").encode("utf-16-le")
                memory = self.k.GlobalAlloc(2, len(data))
                pointer = self.k.GlobalLock(memory)
                if not pointer:
                    self.k.GlobalFree(memory)
                    raise OSError("Clipboard allocation failed.")
                c.memmove(pointer, data, len(data))
                self.k.GlobalUnlock(memory)
                if not self.u.EmptyClipboard():
                    self.k.GlobalFree(memory)
                    raise OSError("Could not replace clipboard.")
                sequence = self.u.GetClipboardSequenceNumber()
                if not self.u.SetClipboardData(13, memory):
                    self.k.GlobalFree(memory)
                    raise OSError("Could not set clipboard.")
            finally:
                self.u.CloseClipboard()
                if sequence is not None:
                    sequence = self.u.GetClipboardSequenceNumber()
            yield
        finally:
            try:
                if sequence is not None:
                    for _ in range(20):
                        if self.u.OpenClipboard(owner):
                            break
                        time.sleep(0.025)
                    else:
                        raise OSError("Clipboard is busy during restore.")
                    try:
                        if self.u.GetClipboardSequenceNumber() == sequence:
                            self.u.EmptyClipboard()
                            for fmt, handle in list(saved.items()):
                                if not self.u.SetClipboardData(fmt, handle):
                                    raise OSError("Clipboard restore failed.")
                                del saved[fmt]
                    finally:
                        self.u.CloseClipboard()
            finally:
                for fmt, handle in saved.items():
                    if fmt in (2, 9, 0x82):
                        c.windll.gdi32.DeleteObject(w.HANDLE(handle))
                    elif fmt in (14, 0x8E):
                        c.windll.gdi32.DeleteEnhMetaFile(w.HANDLE(handle))
                    else:
                        self.k.GlobalFree(handle)
                self.u.DestroyWindow(owner)

    def send(self, hwnd: int, line: str, delay: float) -> None:
        self.focus(hwnd)
        with self.clipboard(line):
            for keys in [
                [(13, 0), (13, 2)],
                [(17, 0), (86, 0), (86, 2), (17, 2)],
                [(13, 0), (13, 2)],
            ]:
                time.sleep(delay)
                if self.u.GetForegroundWindow() != hwnd:
                    raise OSError("Game window lost focus.")
                events = (Input * len(keys))(
                    *[
                        Input(1, Payload(keyboard=Keyboard(key, 0, flags, 0, 0)))
                        for key, flags in keys
                    ]
                )
                if self.u.SendInput(len(events), events, c.sizeof(Input)) != len(events):
                    raise OSError("Windows could not send the travel command.")
            time.sleep(delay)
