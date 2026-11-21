"""Per-user bootstrap, version switch and startup watchdog."""

import ctypes
import logging
import os
import shutil
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path

from gilhound.updater._layout import (
    EXE,
    install_root,
    lock,
    pointer,
    precedence,
    ready,
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


def _start(root: Path, version: str) -> subprocess.Popen:
    folder = root / f"app-{version}"
    return subprocess.Popen(
        [folder / EXE, "--launcher-pid", str(os.getpid())],
        cwd=folder,
        env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
    )


def launch(root: Path) -> int:
    with ExitStack() as leases:
        with lock(root / "install.lock"):
            current = pointer(root, "current.txt")
            pending = pointer(root, "pending.txt")
            if not current:
                raise ValueError("Installation has no current version")
            previous = pointer(root, "previous.txt")
            trial = bool(pending and ready(root / f"app-{pending}"))
            if trial and pending and pending != current:
                previous = current
                write_pointer(root, "previous.txt", previous)
                write_pointer(root, "current.txt", pending)
                current = pending
            _clean(root, {current, pending, previous if trial else None})
            leases.enter_context(lock(root / f"use-{current}.lock", shared=True))
            try:
                app = _start(root, current)
                if trial:
                    try:
                        status = app.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        status = 0
                    if status:
                        raise RuntimeError(f"Startup exited with status {status}")
            except (OSError, RuntimeError):
                if not trial or not previous or previous == current:
                    raise
                logging.exception("Rolling back %s to %s", current, previous)
                write_pointer(root, "current.txt", previous)
                (root / "pending.txt").unlink(missing_ok=True)
                leases.enter_context(lock(root / f"use-{previous}.lock", shared=True))
                app = _start(root, previous)
            if trial:
                (root / "pending.txt").unlink(missing_ok=True)
            (root / "previous.txt").unlink(missing_ok=True)
        return app.wait()


def _shortcuts(root: Path) -> None:
    script = """
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
foreach ($name in 'Programs','Desktop') {
    $link = $shell.CreateShortcut((Join-Path $shell.SpecialFolders.Item($name) 'Gilhound.lnk'))
    $link.TargetPath = Join-Path $env:GILHOUND_SHORTCUT_ROOT 'Gilhound.exe'
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
    bundles = [p for p in source.glob("app-*") if ready(p)]
    if len(bundles) != 1:
        raise ValueError("Extract the entire release zip before running Gilhound")
    bundle = bundles[0]
    version = bundle.name[4:]
    precedence(version)
    with lock(root / "install.lock"):
        launcher = root / "Gilhound.exe"
        if not launcher.exists():
            temp = root / "Gilhound.exe.partial"
            shutil.copyfile(source / launcher.name, temp)
            temp.replace(launcher)
        current = pointer(root, "current.txt")
        if current is None or precedence(version) > precedence(current):
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
    source = Path(sys.executable).resolve().parent
    try:
        if source != root.resolve():
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

