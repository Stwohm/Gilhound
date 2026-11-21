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
from gilhound.updater._layout import acknowledge_startup


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
        try:
            while not server.started:
                if not worker.is_alive():
                    raise RuntimeError("Could not start the local API.")
                time.sleep(0.025)
            window = webview.create_window(
                "Gilhound", f"http://127.0.0.1:{port}/", width=1200, height=800
            )
            if args.ready_marker or getattr(sys, "frozen", False):
                def acknowledge() -> None:
                    if worker.is_alive() and not server.should_exit:
                        acknowledge_startup(__version__, args.ready_marker)

                window.events.shown += acknowledge
            icon = Path(__file__).resolve().parents[1] / "assets" / "gilhound.ico"
            webview.start(
                gui="edgechromium" if sys.platform == "win32" else None,
                icon=str(icon) if icon.is_file() else None,
            )
        finally:
            server.should_exit = True
            worker.join()
    return 0
