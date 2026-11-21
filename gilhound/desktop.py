"""Start the desktop window on a pre-bound loopback socket."""

import argparse
import ctypes
import getpass
import os
import platform
import secrets
import socket
import struct
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from gilhound import __version__
from gilhound.api.app import create_app
from gilhound.runtime.diagnostics import LOG
from gilhound.updater import instance
from gilhound.updater._layout import acknowledge_startup, install_root


def webview2_available(library: Path) -> bool:
    arch = (
        "x86"
        if struct.calcsize("P") == 4
        else ("arm64" if platform.machine().lower() == "arm64" else "x64")
    )
    loader = ctypes.WinDLL(str(library / f"runtimes/win-{arch}/native/WebView2Loader.dll"))
    version = ctypes.c_void_p()
    status = loader.GetAvailableCoreWebView2BrowserVersionString(None, ctypes.byref(version))
    if version:
        ctypes.windll.ole32.CoTaskMemFree(version)
    return status == 0


def serve(port: int) -> None:
    token = os.environ.get("GILHOUND_SESSION_TOKEN") or getpass.getpass("Session bearer token: ")
    uvicorn.run(create_app(token, serve_ui=False), host="127.0.0.1", port=port, access_log=False)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="Gilhound")
    parser.add_argument("--launcher-pid", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--ready-marker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if sys.platform == "win32":
        try:
            if getattr(sys, "frozen", False):
                from gilhound.updater.motw import unblock

                folder = Path(sys.executable).parent
                count = unblock(folder)
                if count:
                    with (folder.parent / "launcher.log").open("a", encoding="utf-8") as log:
                        log.write(f"Desktop removed download marks from {count} files.\n")
            import clr  # noqa: F401 -- initialise pythonnet before starting the API
        except (ImportError, OSError, RuntimeError):
            folder_arg = str(Path(sys.executable).parent).replace("'", "''")
            ctypes.windll.user32.MessageBoxW(
                None,
                "Gilhound could not load its Windows desktop runtime. Install or repair "
                "Microsoft Edge WebView2 Runtime and .NET Framework 4.8. Close Gilhound, "
                "then unblock the installed release in PowerShell and reopen it:\n\n"
                f"Get-ChildItem -LiteralPath '{folder_arg}' "
                "-File -Recurse | Unblock-File",
                "Gilhound", 0x10,
            )
            return 1
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        root = install_root()
        root.mkdir(parents=True, exist_ok=True)
        with instance.show_event(root) as (handle, first):
            if not first and not args.launcher_pid:
                instance.request_show(root, handle)
                return 0
            pid = root / "window.pid"
            pid.write_text(str(os.getpid()))
            try:
                return _window(args, handle)
            finally:
                pid.unlink(missing_ok=True)
    return _window(args)


def _window(args: argparse.Namespace, event: int = 0) -> int:
    import webview

    if sys.platform == "win32" and not webview2_available(Path(webview.__file__).parent / "lib"):
        ctypes.windll.user32.MessageBoxW(
            None,
            "Install the Microsoft Edge WebView2 Runtime and reopen Gilhound.\n\n"
            "https://developer.microsoft.com/microsoft-edge/webview2/",
            "Gilhound",
            0x10,
        )
        return 1
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    webview.settings["ALLOW_DOWNLOADS"] = True
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(secrets.token_urlsafe(32)), host="127.0.0.1", port=port,
                access_log=False, use_colors=False,
            )
        )
        worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]})
        worker.start()
        closing = threading.Event()
        show_worker = None
        try:
            while not server.started:
                if not worker.is_alive():
                    raise RuntimeError("Could not start the local API.")
                time.sleep(0.025)
            window = webview.create_window(
                "Gilhound", f"http://127.0.0.1:{port}/", width=1200, height=800
            )
            if event:
                def listen() -> None:
                    while not window.events.shown.wait(0.2):
                        if closing.is_set():
                            return
                    while not closing.is_set():
                        if instance.wait_show(event) and not closing.is_set():
                            hwnd = window.native.Handle.ToInt64()
                            if instance.minimized(hwnd):
                                window.restore()
                            window.show()
                            instance.foreground(hwnd)

                show_worker = threading.Thread(target=listen, daemon=True)
                show_worker.start()
            if args.ready_marker or getattr(sys, "frozen", False):
                def acknowledge() -> None:
                    if worker.is_alive() and not server.should_exit:
                        acknowledge_startup(__version__, args.ready_marker)
                        LOG.info("Desktop ready")

                window.events.shown += acknowledge
            icon = Path(__file__).resolve().parents[1] / "assets" / "gilhound.ico"
            webview.start(
                gui="edgechromium" if sys.platform == "win32" else None,
                icon=str(icon) if icon.is_file() else None,
            )
        finally:
            closing.set()
            if show_worker:
                show_worker.join(1)
            server.should_exit = True
            worker.join()
    return 0
