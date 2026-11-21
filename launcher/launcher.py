"""Per-user bootstrap, version switch and startup watchdog."""

import ctypes
import logging
import os
import shutil
import subprocess
import sys
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
    write_pointer,
)


def _clean(root: Path, keep: set[str | None]) -> None:
    for folder in root.glob("app-*"):
        version = folder.name[4:]
        if version in keep or not folder.is_dir() or folder.is_symlink() or folder.is_junction():
            continue
        try:
            precedence(version)
            with lock(root / f"use-{version}.lock", timeout=0):
                # Windows also protects a mapped executable after a supervisor crash.
                (folder / EXE).unlink(missing_ok=True)
                shutil.rmtree(folder)
            (root / f"use-{version}.lock").unlink(missing_ok=True)
        except (OSError, ValueError):
            logging.info("Deferred cleanup of %s", version)


def _start(root: Path, version: str, marker: Path | None = None) -> subprocess.Popen:
    folder = root / f"app-{version}"
    args = [folder / EXE, "--launcher-pid", str(os.getpid())]
    if marker:
        args.extend(["--ready-marker", marker])
    return subprocess.Popen(
        args,
        cwd=folder,
        env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
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
    with ExitStack() as leases:
        with lock(root / "install.lock", timeout=75):
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
            if pending:
                fallback = previous if retry else current
                leases.enter_context(lock(root / f"use-{pending}.lock", shared=True))
                marker = root / f"ready-{pending}-{uuid4().hex}"
                try:
                    app = _start(root, pending, marker)
                    _await_startup(app, marker)
                except (OSError, RuntimeError):
                    logging.exception("Rolling back %s to %s", pending, fallback)
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
            _clean(root, {current, previous})
            if app is None:
                leases.enter_context(lock(root / f"use-{current}.lock", shared=True))
                app = _start(root, current)
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
    except Exception:
        logging.exception("Launcher failed")
        ctypes.windll.user32.MessageBoxW(
            None, f"Gilhound could not start. See {root / 'launcher.log'}", "Gilhound", 16
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

