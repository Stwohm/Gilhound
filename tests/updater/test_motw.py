"""Windows release smoke test against the real packaged desktop and WebView2."""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from zipfile import ZipFile

import pytest

from gilhound import __version__

ARCHIVE = Path(__file__).resolve().parents[2] / "dist" / f"Gilhound-{__version__}-win64.zip"


def zone(path: Path) -> Path:
    return Path(str(path) + ":Zone.Identifier")


@pytest.mark.skipif(
    sys.platform != "win32" or not ARCHIVE.exists(), reason="build desktop zip first"
)
def test_frozen_download_reaches_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / ARCHIVE.name
    shutil.copyfile(ARCHIVE, archive)
    zone(archive).write_text("[ZoneTransfer]\nZoneId=3\n")
    source = tmp_path / "extracted"
    with ZipFile(archive) as zipped:
        zipped.extractall(source)
    for file in source.rglob("*"):
        if file.is_file():
            zone(file).write_bytes(zone(archive).read_bytes())

    local = tmp_path / "local"
    root = local / "Gilhound"
    root.mkdir(parents=True)
    monkeypatch.setenv("GILHOUND_SHORTCUT_DIR", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("WEBVIEW2_USER_DATA_FOLDER", str(tmp_path / "webview"))
    for proxy in ("HTTP_PROXY", "HTTPS_PROXY"):
        monkeypatch.setenv(proxy, "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    # The release bootstrap exits after starting the installed supervisor.
    # Identify that supervisor by its exact path to close only this test's process tree.
    monkeypatch.setenv("GILHOUND_TEST_SUPERVISOR", str(root / "Gilhound.exe"))
    try:
        subprocess.run([source / "Gilhound.exe"], cwd=source, check=True, timeout=60)
        log = tmp_path / "roaming/Gilhound/logs/gilhound.log"
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if log.exists() and "Desktop ready" in log.read_text(encoding="utf-8"):
                break
            time.sleep(0.15)
        else:
            pytest.fail("Installed desktop did not acknowledge its window")
    finally:
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "Get-Process -Name Gilhound -ErrorAction SilentlyContinue | "
                "Where-Object { $_.Path -eq $env:GILHOUND_TEST_SUPERVISOR } | "
                "ForEach-Object { taskkill /T /F /PID $_.Id }; exit 0",
            ],
            check=True,
        )
    folder = root / f"app-{__version__}"
    assert not any(zone(f).exists() for f in root.rglob("*") if f.is_file())

    dll = next(folder.rglob("Python.Runtime.dll"))
    zone(dll).write_bytes(zone(archive).read_bytes())
    marker = tmp_path / "ready-repaired"
    with subprocess.Popen(
        [folder / "gilhound-app.exe", "--ready-marker", marker], cwd=folder, env=os.environ.copy()
    ) as desktop:
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and desktop.poll() is None:
                if marker.exists():
                    break
                time.sleep(0.15)
            assert marker.exists(), "Repaired desktop did not acknowledge its window"
            assert not zone(dll).exists()
        finally:
            if desktop.poll() is None:
                subprocess.run(["taskkill", "/PID", str(desktop.pid), "/T", "/F"], check=True)
