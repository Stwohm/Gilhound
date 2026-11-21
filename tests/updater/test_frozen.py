"""Offline Windows integration using the 0.1.5 and 0.1.6 build.ps1 -Stub archives."""

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path
from zipfile import ZipFile

import pytest

REPO = Path(__file__).resolve().parents[2]
ARCHIVES = REPO / "dist/stub"
OLD = ARCHIVES / "Gilhound-0.1.5-win64.zip"
NEW = ARCHIVES / "Gilhound-0.1.6-win64.zip"
DESKTOP = REPO / "dist" / NEW.name


def wait_for_start(root: Path, count: int) -> None:
    log = root / "stub-starts.txt"
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if log.exists() and log.read_text().splitlines() == ["0.1.6"] * count:
            return
        time.sleep(0.1)
    pytest.fail((root / "launcher.log").read_text())


@pytest.mark.skipif(sys.platform != "win32" or not all(p.exists() for p in (OLD, NEW, DESKTOP)),
                    reason="requires Windows, both stub archives and the 0.1.6 desktop archive")
def test_frozen_upgrade_and_shortcut(tmp_path: Path) -> None:
    release = tmp_path / "release"
    local = tmp_path / "Local AppData"
    root = local / "Gilhound"
    env = {**os.environ, "LOCALAPPDATA": str(local), "APPDATA": str(tmp_path / "Roaming"),
           "GILHOUND_SHORTCUT_DIR": str(tmp_path), "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    with ZipFile(NEW) as archive:
        archive.extractall(release)
    # Use the exact shipping launcher for installation, direct launch and the shortcut.
    # The frozen stub desktop acknowledges startup without opening a window or calling the feed.
    with ZipFile(DESKTOP) as archive:
        for member in ("Gilhound.exe", "app-0.1.6/Gilhound.exe"):
            archive.extract(member, release)
    run = subprocess.run
    run([release / "Gilhound.exe"], cwd=release, env=env, check=True, timeout=45)
    wait_for_start(root, 1)
    # Both entry points must launch, even with four READY folders and differing prefixes.
    alias = {**env, "LOCALAPPDATA": "\\\\?\\" + str(local)}
    for version in ("0.1.1", "0.1.3", "0.1.4"):
        folder = root / f"app-{version}"
        folder.mkdir()
        (folder / "READY").touch()
        (folder / "gilhound-app.exe").touch()
    run([root / "Gilhound.exe"], cwd=root, env=alias, check=True, timeout=45)
    wait_for_start(root, 2)
    run(["powershell.exe", "-NoProfile", "-Command",
         "$p = Start-Process -FilePath './Gilhound.lnk' -PassThru -Wait; exit $p.ExitCode"],
        cwd=tmp_path, env=alias, check=True, timeout=45)
    wait_for_start(root, 3)
    assert "samefile=True" in (root / "launcher.log").read_text()

    # Exercise the shipped 0.1.5 updater and supervisor in a separate installed layout.
    local = tmp_path / "legacy"
    root = local / "Gilhound"
    env["LOCALAPPDATA"] = "\\\\?\\" + str(local)
    with ZipFile(OLD) as archive:
        archive.extractall(root)
    (root / "current.txt").write_text("0.1.5")
    for version in ("0.1.1", "0.1.3", "0.1.4"):
        folder = root / f"app-{version}"
        folder.mkdir()
        (folder / "READY").touch()
        (folder / "gilhound-app.exe").touch()
    legacy = tmp_path / "legacy_updater.py"
    legacy.write_bytes(subprocess.check_output(
        ["git", "show", "v0.1.5:gilhound/updater/__init__.py"], cwd=REPO))
    checksum = tmp_path / "SHA256SUMS.txt"
    checksum.write_text(f"{hashlib.sha256(NEW.read_bytes()).hexdigest()}  {NEW.name}")
    run([sys.executable, "-c", """
import runpy, sys
from urllib.request import urlopen
old = runpy.run_path(sys.argv[1])
stage = old["download_and_stage"]
stage.__globals__["_open"] = urlopen
stage(old["UpdateInfo"]("0.1.6", sys.argv[2], sys.argv[3]))
old["apply_and_restart"]()
""", legacy, NEW.as_uri(), checksum.as_uri()], cwd=REPO, env=env, check=True, timeout=45)
    wait_for_start(root, 1)
    with ZipFile(NEW) as archive:
        assert (root / "Gilhound.exe").read_bytes() == archive.read("app-0.1.6/Gilhound.exe")
    run([root / "Gilhound.exe"], cwd=root, env=env, check=True, timeout=45)
    assert (root / "current.txt").read_text() == "0.1.6"
    assert (root / "previous.txt").read_text() == "0.1.5"
    assert not (root / "pending.txt").exists()
    assert sorted(p.name for p in root.glob("app-*")) == ["app-0.1.5", "app-0.1.6"]

    # Resume from the files left when an old launcher fails before starting the trial.
    with ZipFile(OLD) as archive:
        archive.extract("Gilhound.exe", root)
    (root / "current.txt").write_text("0.1.5")
    (root / "pending.txt").write_text("0.1.6")
    staged_launcher = root / "app-0.1.6/Gilhound.exe"
    run([staged_launcher], cwd=root, env=env, check=True, timeout=45)
    assert (root / "Gilhound.exe").read_bytes() == staged_launcher.read_bytes()
    assert (root / "current.txt").read_text() == "0.1.6"
    assert not (root / "pending.txt").exists()
