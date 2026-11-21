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

@pytest.mark.skipif(sys.platform != "win32", reason="requires the Windows desktop")
def test_frozen_second_launch_restores_window(tmp_path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    from gilhound import __version__
    from gilhound.updater.instance import show_event

    bundle = REPO / f"dist/Gilhound-{__version__}-win64.zip"
    if not bundle.exists():
        pytest.skip("build the current desktop archive with packaging/build.ps1")
    local = tmp_path / "Local AppData"
    root = local / "Gilhound"
    roaming = tmp_path / "Roaming"
    settings = roaming / "Gilhound/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"home_world":"Cerberus","buy_region":"Europe","auto_snipe_enabled":false}')
    env = {**os.environ, "LOCALAPPDATA": str(local), "APPDATA": str(roaming),
           "GILHOUND_UPDATE_FEED": "offline:", "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    with ZipFile(bundle) as archive:
        archive.extractall(root)
    (root / "current.txt").write_text(__version__)

    user = ctypes.windll.user32
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    windows = []
    pid = None

    @callback
    def collect(hwnd: int, _: int) -> bool:
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user.IsWindowVisible(hwnd):
            caption = ctypes.create_unicode_buffer(256)
            user.GetWindowTextW(hwnd, caption, len(caption))
            if caption.value == "Gilhound":
                windows.append(hwnd)
        return True

    args = [root / "Gilhound.exe"]
    pidfile = root / "window.pid"

    def wait_for_window(supervisor: subprocess.Popen, timeout: float) -> int:
        nonlocal pid
        windows.clear()
        deadline = time.monotonic() + timeout
        while not windows and time.monotonic() < deadline:
            assert supervisor.poll() is None
            if pidfile.exists() and (value := pidfile.read_text()).isdigit():
                pid = int(value)
                user.EnumWindows(collect, 0)
            time.sleep(0.05)
        assert windows, (root / "launcher.log").read_text()
        return windows[0]

    first = subprocess.Popen(args, env=env, cwd=root)
    launches = [first]
    try:
        hwnd = wait_for_window(first, 60)
        user.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        assert user.IsIconic(hwnd)
        leases = list(root.glob("lease-*.lock"))
        assert len(leases) == 1
        start = time.monotonic()
        subprocess.run(args, env=env, cwd=root, timeout=5, check=True)
        elapsed = time.monotonic() - start
        print(f"Frozen second launch: {elapsed:.3f}s")
        assert elapsed < 1.5
        deadline = time.monotonic() + 2
        while user.GetForegroundWindow() != hwnd and time.monotonic() < deadline:
            time.sleep(0.025)
        assert user.IsWindow(hwnd) and not user.IsIconic(hwnd)
        assert user.GetForegroundWindow() == hwnd
        assert int(pidfile.read_text()) == pid and first.poll() is None
        assert list(root.glob("lease-*.lock")) == leases
        assert (root / "launcher.log").read_text().count(f"Starting app-{__version__}") == 1
        assert f"focused existing pid {pid}" in (root / "launcher.log").read_text()
        old_pid = pid
        # Reproduce a lingering show handle even if the first supervisor exits quickly.
        with show_event(root):
            assert user.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
            deadline = time.monotonic() + 5
            while user.IsWindow(hwnd) and time.monotonic() < deadline:
                time.sleep(0.025)
            assert not user.IsWindow(hwnd) and not pidfile.exists()
            reopened = subprocess.Popen(args, env=env, cwd=root)
            launches.append(reopened)
            hwnd = wait_for_window(reopened, 8)
            assert pid != old_pid
            assert first.wait(5) == 0
            assert int(pidfile.read_text()) == pid
            assert (root / "launcher.log").read_text().count(f"Starting app-{__version__}") == 2
            assert user.PostMessageW(hwnd, 0x0010, 0, 0)
            assert reopened.wait(5) == 0 and not pidfile.exists()
    finally:
        for process in launches:
            if process.poll() is None:
                subprocess.run(["taskkill", "/pid", str(process.pid), "/t", "/f"],
                               capture_output=True)
                process.wait(10)
