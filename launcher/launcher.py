"""Per-user bootstrap, version switch and startup watchdog."""

import ctypes
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from gilhound.updater._layout import (
    EXE,
    install_root,
    lock,
    pointer,
    precedence,
    ready,
    rejected,
    replace_launcher,
    same_location,
    version_lease,
    write_pointer,
)
from gilhound.updater.instance import request_show, show_event, window_pid
from gilhound.updater.motw import unblock


def _clean(root: Path, keep: set[str | None]) -> list[Path]:
    """Retire unused bundles under install.lock. Delete their contents after unlocking."""
    retired = [
        folder for folder in root.glob("retired-app-*")
        if re.fullmatch(r"retired-app-[0-9a-f]{32}", folder.name)
        and folder.is_dir() and not folder.is_junction() and not folder.is_symlink()
    ]
    busy = set()
    for path in (*root.glob("lease-*.lock"), *root.glob("use-*.lock")):
        prefix, name = path.stem.split("-", 1)
        version = name.rsplit("-", 1)[0] if prefix == "lease" else name
        try:
            with lock(path, timeout=0):
                pass
            path.unlink(missing_ok=True)
        except OSError:
            busy.add(version)
    for folder in root.glob("app-*"):
        version = folder.name[4:]
        if version in keep or version in busy:
            continue
        if not folder.is_dir() or folder.is_symlink() or folder.is_junction():
            continue
        try:
            precedence(version)
            # Windows protects a mapped executable even after a legacy supervisor crash.
            (folder / EXE).unlink(missing_ok=True)
            target = root / f"retired-app-{uuid4().hex}"
            folder.rename(target)
            retired.append(target)
        except (OSError, ValueError):
            logging.info("Deferred cleanup of %s", version)
    return retired


def _start(root: Path, version: str, marker: Path | None = None) -> subprocess.Popen:
    folder = root / f"app-{version}"
    args = [folder / EXE, "--launcher-pid", str(os.getpid())]
    if marker:
        args.extend(["--ready-marker", marker])
    env = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    env.pop("GILHOUND_RESTART_VERSION", None)
    logging.info("Starting app-%s", version)
    return subprocess.Popen(
        args,
        cwd=folder,
        env=env,
    )


def _await_startup(app: subprocess.Popen, marker: Path) -> None:
    deadline = time.monotonic() + 60
    while not marker.is_file():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            app.kill()
            app.wait()
            raise RuntimeError("No startup acknowledgement within 60 seconds")
        try:
            status = app.wait(timeout=min(0.25, remaining))
        except subprocess.TimeoutExpired:
            continue
        # The child may acknowledge and exit during the same poll.
        if not marker.is_file():
            raise RuntimeError(f"Startup exited with status {status} before acknowledgement")


def _read_pointer(root: Path, name: str) -> str | None:
    try:
        return pointer(root, name)
    except ValueError:
        logging.warning("Ignoring malformed %s", name)
        return None


def _bundles(root: Path) -> list[str]:
    versions = []
    for folder in root.glob("app-*"):
        version = folder.name[4:]
        try:
            precedence(version)
        except ValueError:
            continue
        if ready(folder):
            versions.append(version)
    return sorted(versions, key=precedence, reverse=True)


def launch(root: Path) -> int:
    restart = os.environ.get("GILHOUND_RESTART_VERSION")
    with ExitStack() as leases:
        with lock(root / "install.lock"):
            while True:
                with lock(root / "window.lock"):
                    pid = window_pid(root)
                    if pid is None:
                        break
                    if not restart or _read_pointer(root, "pending.txt") != restart:
                        with show_event(root) as event:
                            request_show(pid, event)
                        return 0
                time.sleep(0.05)
            versions = _bundles(root)
            current = _read_pointer(root, "current.txt")
            pending = _read_pointer(root, "pending.txt")
            previous = _read_pointer(root, "previous.txt")
            failed = pointer(root, "failed.txt")
            if current not in versions or current == failed:
                usable = [v for v in versions if v != failed]
                current = usable[0] if usable else None
                if current == pending and len(usable) > 1:
                    current = usable[1]  # Keep a rollback target for the pending trial.
                if current is None:
                    raise ValueError("Installation has no usable READY bundle")
                write_pointer(root, "current.txt", current)
            if previous not in versions or previous == current or previous == failed:
                previous = None
                (root / "previous.txt").unlink(missing_ok=True)
            # Legacy trials wrote current before acknowledgement. Retry the trial without
            # moving the pointer backwards unless the new process actually fails.
            retry = previous and current == pending and precedence(previous) < precedence(current)
            if (pending not in versions or rejected(root, pending)
                    or (not retry and precedence(pending) <= precedence(current))):
                (root / "pending.txt").unlink(missing_ok=True)
                pending = None
            app = None
            rollback = None
            if pending:
                fallback = previous if retry else current
                leases.enter_context(version_lease(root, pending))
                marker = root / f"ready-{pending}-{uuid4().hex}"
                try:
                    app = _start(root, pending, marker)
                    _await_startup(app, marker)
                except (OSError, RuntimeError):
                    logging.exception("Rolling back %s to %s", pending, fallback)
                    rollback = (f"Gilhound v{pending} failed to start. Reopened v{fallback}. "
                                f"See {root / 'launcher.log'} for details.")
                    write_pointer(root, "failed.txt", pending)
                    if retry:
                        current = fallback
                        write_pointer(root, "current.txt", current)
                    app = None
                else:
                    previous = fallback
                    write_pointer(root, "previous.txt", previous)
                    write_pointer(root, "current.txt", pending)
                    current = pending
                finally:
                    marker.unlink(missing_ok=True)
                (root / "pending.txt").unlink(missing_ok=True)
            if app is None:
                leases.enter_context(version_lease(root, current))
                app = _start(root, current)
                # Serialize simultaneous shortcut clicks until the desktop publishes its PID.
                if getattr(sys, "frozen", False):
                    deadline = time.monotonic() + 60
                    while not (root / "window.pid").exists() and app.poll() is None:
                        if time.monotonic() >= deadline:
                            break
                        time.sleep(0.05)
            retired = _clean(root, {current, previous})
        if rollback and getattr(sys, "frozen", False):
            threading.Thread(
                target=ctypes.windll.user32.MessageBoxW,
                args=(None, rollback, "Gilhound update", 0x30), daemon=True,
            ).start()
        for folder in retired:
            try:
                shutil.rmtree(folder)
            except FileNotFoundError:
                pass
            except OSError:
                logging.info("Deferred removal of %s", folder.name)
        return app.wait()


def _shortcuts(root: Path) -> None:
    script = """
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
$dirs = if ($env:GILHOUND_SHORTCUT_DIR) { $env:GILHOUND_SHORTCUT_DIR } else {
    $shell.SpecialFolders.Item('Programs')
    $shell.SpecialFolders.Item('Desktop')
}
foreach ($dir in $dirs) {
    $link = $shell.CreateShortcut((Join-Path $dir 'Gilhound.lnk'))
    $link.TargetPath = Join-Path $env:GILHOUND_SHORTCUT_ROOT 'Gilhound.exe'
    $link.IconLocation = "$($link.TargetPath),0"
    $link.WorkingDirectory = $env:GILHOUND_SHORTCUT_ROOT
    $link.Save()
}
"""
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True, creationflags=subprocess.CREATE_NO_WINDOW,
        env={**os.environ, "GILHOUND_SHORTCUT_ROOT": str(root)},
    )


def install(source: Path, root: Path) -> None:
    bundles = _bundles(source)
    if not bundles:
        raise ValueError("Extract the entire release zip before running Gilhound")
    version = bundles[0]
    bundle = source / f"app-{version}"
    with lock(root / "install.lock"):
        current = _read_pointer(root, "current.txt")
        pending = _read_pointer(root, "pending.txt")
        newer_pending = pending and ready(root / f"app-{pending}") and (
            precedence(pending) > precedence(version)
        )
        if not newer_pending and (current is None or precedence(version) >= precedence(current)):
            launcher = bundle / "Gilhound.exe"
            replace_launcher(launcher if launcher.is_file() else source / launcher.name, root)
        if not newer_pending and (current is None or (precedence(version) > precedence(current)
                                                      and not rejected(root, version))):
            folder = root / bundle.name
            if not ready(folder):
                partial = root / "install.partial"
                if partial.exists():
                    shutil.rmtree(partial)
                shutil.copytree(bundle, partial, ignore=shutil.ignore_patterns("READY"))
                unblock(partial)
                (partial / "READY").touch()
                if folder.exists():
                    shutil.rmtree(folder)
                partial.rename(folder)
            write_pointer(root, "pending.txt" if current else "current.txt", version)
        _shortcuts(root)


def main() -> int:
    root = install_root()
    root.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=root / "launcher.log", level=logging.INFO)
    source = Path(sys.executable).parent
    try:
        try:
            same = os.path.samefile(source, root)
        except OSError as exc:
            same = str(exc)
        logging.info("sys.executable=%r source=%r root=%r source.resolve=%r root.resolve=%r "
                     "samefile=%r", sys.executable, str(source), str(root), str(source.resolve()),
                     str(root.resolve()), same)
        if source.name.startswith("app-") and same_location(source.parent, root):
            # A staged launcher can also resume an update a legacy launcher could not start.
            with lock(root / "install.lock"):
                selected = _read_pointer(root, "pending.txt") or _read_pointer(root, "current.txt")
                if source.name[4:] == selected:
                    replace_launcher(source / "Gilhound.exe", root)
            return launch(root)
        if not same_location(source, root):
            install(source, root)
            subprocess.Popen(
                [root / "Gilhound.exe"], cwd=root,
                env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
            )
            return 0
        return launch(root)
    except Exception as exc:
        logging.exception("Launcher failed: %s: %s", type(exc).__name__, exc)
        ctypes.windll.user32.MessageBoxW(
            None, f"Gilhound could not start. See {root / 'launcher.log'}", "Gilhound", 16
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

