import hashlib
import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

import gilhound
from gilhound import updater
from gilhound.updater._layout import EXE, lock, precedence, replace_launcher, same_location

# The onefile launcher deliberately lives outside the installed Python package.
script = Path(__file__).resolve().parents[2] / "launcher" / "launcher.py"
spec = importlib.util.spec_from_file_location("gilhound_launcher", script)
assert spec and spec.loader
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)

APP = """import sys
from pathlib import Path
assert sys.argv[1] == '--launcher-pid' and int(sys.argv[2]) > 0
with Path('../started').open('a') as log:
    print(Path.cwd().name, file=log)
if {0} == 0 and '--ready-marker' in sys.argv:
    Path(sys.argv[-1]).touch()
raise SystemExit({0})
"""


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(gilhound, "__version__", "0.1.0")
    root = tmp_path / "Gilhound"
    old = root / "app-0.1.0"
    old.mkdir(parents=True)
    (old / "READY").touch()
    (old / EXE).write_text(APP.format(0))
    (root / "current.txt").write_text("0.1.0")

    # Run the stand-in executable with Python, preserving the production argv and cwd.
    popen = subprocess.Popen

    def run_script(args: list, **kwargs: object) -> subprocess.Popen:
        return popen(args if args[0] == sys.executable else [sys.executable, *args], **kwargs)

    monkeypatch.setattr(launcher.subprocess, "Popen", run_script)
    return root


@pytest.fixture
def feed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "feed"
    path.mkdir()
    monkeypatch.setattr(updater, "_open", urlopen)
    monkeypatch.setenv("GILHOUND_UPDATE_FEED", (path / "latest").as_uri())
    return path


def release(
    feed: Path, status: int = 0, bad_member: bool = False, version: str = "0.1.1",
) -> updater.UpdateInfo:
    names = [f"Gilhound-{version}-win64.zip", "SHA256SUMS.txt"]
    assets = [dict(name=name, browser_download_url=(feed / name).as_uri()) for name in names]
    (feed / "latest").write_text(json.dumps(dict(tag_name=f"v{version}", assets=assets)))
    info = updater.check()
    assert info is not None and info.version == version
    path = feed / info.filename
    with ZipFile(path, "w") as archive:
        archive.writestr("Gilhound.exe", "launcher")
        archive.writestr("install.ps1", "")
        archive.writestr(f"app-{version}/READY", "")
        archive.writestr(f"app-{version}/{EXE}", APP.format(status))
        if bad_member:
            archive.writestr("app-0.1.1/../escape", "")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (feed / "SHA256SUMS.txt").write_text(f"{digest}  {path.name}\n")
    return info


def test_semver() -> None:
    versions = ["1.2.0-alpha", "1.2.0-alpha.2", "1.2.0-alpha.11", "1.2.0-beta",
                "1.2.0", "1.10.0", "2.0.0"]
    assert sorted(reversed(versions), key=precedence) == versions
    assert precedence("1.0.0+foo") == precedence("1.0.0+bar")


@pytest.mark.skipif(sys.platform != "win32", reason="checks Windows process identity")
@pytest.mark.parametrize("dead", [True, False], ids=["dead-pid", "reused-unrelated-pid"])
def test_stale_window_pid_starts_app(
    layout: Path, dead: bool, caplog: pytest.LogCaptureFixture,
) -> None:
    from gilhound.updater.instance import show_event

    pid = os.getpid()
    if dead:
        with subprocess.Popen([sys.executable, "-c", "pass"]) as child:
            pid = child.pid
        assert child.returncode == 0
    pidfile = layout / "window.pid"
    pidfile.write_text(str(pid))
    with show_event(layout), caplog.at_level(logging.INFO):
        assert launcher.launch(layout) == 0
    assert not pidfile.exists()
    assert (layout / "started").read_text().splitlines() == ["app-0.1.0"]
    assert caplog.messages.count("stale instance file removed") == 1
    assert caplog.messages.count("Starting app-0.1.0") == 1


@pytest.mark.parametrize("version", ["../0.1.1", "1.2", "01.2.3", "1.2.3-01"])
def test_bad_version(version: str) -> None:
    with pytest.raises(ValueError):
        precedence(version)


def test_update_cycle(layout: Path, feed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    progress = []
    info = release(feed)
    for old_version in ("0.0.1", "0.0.2"):
        shutil.copytree(layout / "app-0.1.0", layout / f"app-{old_version}")
    updater.download_and_stage(info, lambda done, total: progress.append((done, total)))
    assert progress[-1][0] == progress[-1][1] > 0
    assert (layout / "current.txt").read_text() == "0.1.0"
    assert (layout / "app-0.1.1/READY").is_file()
    assert (layout / "pending.txt").read_text() == "0.1.1"
    assert launcher.launch(layout) == 0
    old = layout / "app-0.1.0"
    assert old.is_dir() and not (layout / "pending.txt").exists()
    assert (layout / "current.txt").read_text() == "0.1.1"
    with lock(layout / "use-0.1.0.lock"):
        assert launcher.launch(layout) == 0
        assert old.is_dir()
    assert launcher.launch(layout) == 0
    assert old.exists() and (layout / "previous.txt").read_text() == "0.1.0"
    assert sorted(p.name for p in layout.glob("app-*")) == ["app-0.1.0", "app-0.1.1"]
    assert (layout / "started").read_text().splitlines() == ["app-0.1.1"] * 3
    monkeypatch.setattr(gilhound, "__version__", "0.1.1")
    assert updater.check() is None


def test_rollback(layout: Path, feed: Path, caplog: pytest.LogCaptureFixture) -> None:
    bad = release(feed, status=5)
    updater.download_and_stage(bad)
    assert launcher.launch(layout) == 0
    assert "Rolling back 0.1.1 to 0.1.0" in caplog.text
    assert (layout / "current.txt").read_text() == "0.1.0"
    assert (layout / "started").read_text().splitlines() == ["app-0.1.1", "app-0.1.0"]
    assert not (layout / "pending.txt").exists()
    assert updater.check() is None
    with pytest.raises(ValueError, match="previously failed startup"):
        updater.download_and_stage(bad)
    # Older installed clients may still stage a rejected release.
    (layout / "pending.txt").write_text(bad.version)
    assert launcher.launch(layout) == 0
    assert (layout / "started").read_text().splitlines()[-1] == "app-0.1.0"
    updater.download_and_stage(release(feed, version="0.1.2"))
    assert launcher.launch(layout) == 0
    assert (layout / "current.txt").read_text() == "0.1.2"


@pytest.mark.parametrize("corrupt", [True, False])
def test_rejected_archive(layout: Path, feed: Path, corrupt: bool) -> None:
    info = release(feed, bad_member=not corrupt)
    if corrupt:
        (feed / info.filename).write_bytes(b"truncated")
    with pytest.raises(ValueError, match="SHA256 mismatch" if corrupt else "archive path"):
        updater.download_and_stage(info)
    for name in ("pending.txt", "app-0.1.1", "app-0.1.1.partial", "escape"):
        assert not (layout / name).exists()
    assert (layout / "current.txt").read_text() == "0.1.0"


def test_unready_pending(layout: Path) -> None:
    (layout / "pending.txt").write_text("0.1.1")
    (layout / "app-0.1.1").mkdir()
    assert launcher.launch(layout) == 0
    assert (layout / "started").read_text().strip() == "app-0.1.0"


def test_restart(layout: Path, feed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="No complete update"):
        updater.apply_and_restart()
    updater.download_and_stage(release(feed))
    started = []

    def spawn(args: list, **options: object) -> None:
        started.append((args, options))

    def exit_process(status: int) -> None:
        raise SystemExit(status)

    monkeypatch.setattr(updater.subprocess, "Popen", spawn)
    monkeypatch.setattr(updater.os, "_exit", exit_process)
    with pytest.raises(SystemExit) as exit_info:
        updater.apply_and_restart()
    assert exit_info.value.code == 0
    [(args, options)] = started
    assert args == [layout / "Gilhound.exe"]
    assert options["cwd"] == layout
    assert options["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def test_first_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "extracted"
    folder = source / "app-0.1.0"
    folder.mkdir(parents=True)
    (source / "Gilhound.exe").write_bytes(b"launcher")
    (folder / EXE).write_bytes(b"desktop")
    (folder / "READY").touch()
    shutil.copytree(folder, source / "app-0.0.9")
    shutil.copytree(folder, source / "app-invalid")
    root = tmp_path / "installed"
    root.mkdir()
    shortcuts = []
    monkeypatch.setattr(launcher, "_shortcuts", shortcuts.append)
    launcher.install(source, root)
    (root / "Gilhound.exe").write_bytes(b"legacy launcher")
    launcher.install(source, root)
    assert shortcuts == [root, root]
    assert (root / "current.txt").read_text() == "0.1.0"
    assert (root / folder.name / "READY").is_file()
    assert (root / folder.name / EXE).read_bytes() == b"desktop"
    assert (root / "Gilhound.exe").read_bytes() == b"launcher"


def test_reinstall_keeps_newer_staged_release(
    layout: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = layout.parent / "extracted"
    shutil.copytree(layout / "app-0.1.0", source / "app-0.1.1")
    shutil.copytree(layout / "app-0.1.0", layout / "app-0.1.2")
    (layout / "pending.txt").write_text("0.1.2")
    (source / "Gilhound.exe").write_bytes(b"older launcher")
    (layout / "Gilhound.exe").write_bytes(b"newer launcher")
    monkeypatch.setattr(launcher, "_shortcuts", lambda root: None)
    launcher.install(source, layout)
    assert (layout / "pending.txt").read_text() == "0.1.2"
    assert (layout / "Gilhound.exe").read_bytes() == b"newer launcher"


def test_same_location_falls_back_to_resolved_name(tmp_path: Path) -> None:
    assert same_location(tmp_path / "missing", tmp_path / "child/../missing")
    assert not same_location(tmp_path / "missing", tmp_path / "other")


def test_swap_restores_running_launcher_on_error(
    layout: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = layout / "Gilhound.exe"
    old.write_bytes(b"old")
    new = layout / "app-0.1.0/Gilhound.exe"
    new.write_bytes(b"new")

    def deny_replace(self: Path, target: Path) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(Path, "replace", deny_replace)
    with pytest.raises(PermissionError):
        replace_launcher(new, layout)
    assert old.read_bytes() == b"old"
    assert not (layout / "Gilhound.exe.partial").exists()


@pytest.mark.parametrize("broken", [None, "", "../invalid", "9.9.9", "0.1.10"])
def test_recover_current(layout: Path, broken: str | None) -> None:
    for version in ("0.1.2", "0.1.9", "0.1.10"):
        shutil.copytree(layout / "app-0.1.0", layout / f"app-{version}")
    (layout / "failed.txt").write_text("0.1.10")
    current = layout / "current.txt"
    if broken is None:
        current.unlink()
    else:
        current.write_text(broken)
    assert launcher.launch(layout) == 0
    assert current.read_text() == "0.1.9"
    assert (layout / "started").read_text().strip() == "app-0.1.9"


def test_stale_pending_does_not_downgrade(layout: Path) -> None:
    shutil.copytree(layout / "app-0.1.0", layout / "app-0.0.9")
    (layout / "pending.txt").write_text("0.0.9")
    assert launcher.launch(layout) == 0
    assert (layout / "current.txt").read_text() == "0.1.0"
    assert not (layout / "pending.txt").exists()


@pytest.mark.parametrize("exit_code", [0, 1])
def test_interrupted_legacy_trial(layout: Path, exit_code: int) -> None:
    bundle = layout / "app-0.1.1"
    shutil.copytree(layout / "app-0.1.0", bundle)
    (bundle / EXE).write_text(
        "from pathlib import Path\nassert Path('../current.txt').read_text() == '0.1.1'\n"
        + APP.format(exit_code)
    )
    for name, value in (("current", "0.1.1"), ("previous", "0.1.0"), ("pending", "0.1.1")):
        (layout / f"{name}.txt").write_text(value)
    assert launcher.launch(layout) == 0
    assert (layout / "current.txt").read_text() == ("0.1.0" if exit_code else "0.1.1")


@pytest.mark.parametrize(
    ("exit_at", "exit_code", "ack_at", "accepted"),
    [
        pytest.param(55, 0, 45, True, id="ack-in-time"),
        pytest.param(5, 1, None, False, id="crash-at-5s"),
        pytest.param(30, 1, None, False, id="crash-at-30s"),
        pytest.param(5, 0, None, False, id="clean-exit-before-ack"),
        pytest.param(120, 0, None, False, id="timeout"),
        pytest.param(55, 1, 30, True, id="crash-after-ack"),
    ],
)
def test_startup_watchdog(
    layout: Path, monkeypatch: pytest.MonkeyPatch, exit_at: int,
    exit_code: int, ack_at: int | None, accepted: bool,
) -> None:
    shutil.copytree(layout / "app-0.1.0", layout / "app-0.1.1")
    (layout / "pending.txt").write_text("0.1.1")
    elapsed = 0.0
    started = []
    killed = False

    class Process:
        def __init__(self, args: list) -> None:
            self.args = args

        def wait(self, timeout: float | None = None) -> int:
            nonlocal elapsed
            if self.args[0].parent.name == "app-0.1.0" or killed:
                return 0
            if timeout is None:
                return exit_code
            assert (layout / "pending.txt").is_file()
            assert (layout / "current.txt").read_text() == "0.1.0"
            assert (layout / "app-0.1.0" / EXE).is_file()
            elapsed = min(elapsed + timeout, exit_at)
            if ack_at is not None and elapsed >= ack_at and "--ready-marker" in self.args:
                Path(self.args[-1]).touch()
            if elapsed == exit_at:
                return exit_code
            raise subprocess.TimeoutExpired(self.args, timeout)

        def kill(self) -> None:
            nonlocal killed
            killed = True

    def spawn(args: list, **kwargs: object) -> Process:
        started.append(args[0].parent.name)
        return Process(args)

    monkeypatch.setattr(time, "monotonic", lambda: elapsed)
    monkeypatch.setattr(launcher.subprocess, "Popen", spawn)
    assert launcher.launch(layout) == (exit_code if accepted else 0)
    assert (layout / "current.txt").read_text() == ("0.1.1" if accepted else "0.1.0")
    assert started == (["app-0.1.1"] if accepted else ["app-0.1.1", "app-0.1.0"])
    assert killed == (exit_at > 60 and ack_at is None)
    assert elapsed == (ack_at if accepted else min(60, exit_at))
    assert not (layout / "pending.txt").exists()


@pytest.mark.parametrize("file", ["README.md", "docs/INSTALL.md"])
def test_travel_disclosure(file: str) -> None:
    guide = (script.parents[1] / file).read_text(encoding="utf-8").lower()
    for term in ("optional", "restocky", "**go**", "/li <world> market", "per click", "clipboard"):
        assert term in guide, file


def test_clean_live_and_crashed_leases(layout: Path) -> None:
    from gilhound.updater._layout import version_lease

    unused = layout / "app-0.0.8"
    shutil.copytree(layout / "app-0.1.0", unused)
    legacy = layout / "use-0.1.0.lock"
    legacy.touch()
    with version_lease(layout, "0.0.8"):
        launcher._clean(layout, {"0.1.0"})
        assert (unused / EXE).is_file()
        assert not legacy.exists()

    crashed = subprocess.run(
        [sys.executable, "-c",
         "from gilhound.updater._layout import version_lease; from pathlib import Path; "
         "import os,sys; lease=version_lease(Path(sys.argv[1]), '0.0.8'); "
         "lease.__enter__(); os._exit(3)", str(layout)],
        cwd=script.parents[1],
    )
    assert crashed.returncode == 3 and list(layout.glob("lease-*.lock"))
    assert launcher.launch(layout) == 0
    assert not unused.exists() and not list(layout.glob("lease-*.lock"))


@pytest.mark.skipif(sys.platform != "win32", reason="requires the Windows instance protocol")
def test_download_and_restart_from_api(
    layout: Path, feed: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from threading import Event

    from gilhound.api import updates
    from gilhound.updater._layout import version_lease

    # Stand in only for the two frozen executable entry points; both use the
    # production launcher protocol, including a separate restart process.
    (layout / "app-0.1.0").rename(layout / "app-0.2.1")
    (layout / "current.txt").write_text("0.2.1")
    monkeypatch.setattr(gilhound, "__version__", "0.2.1")
    info = release(feed, version="0.2.2")
    with ZipFile(feed / info.filename, "a") as archive:
        archive.writestr(f"app-{info.version}/Gilhound.exe", f"""
import runpy, sys, subprocess
from pathlib import Path
from gilhound.updater import instance
entry = runpy.run_path({str(script)!r})
spawn = subprocess.Popen
subprocess.Popen = lambda args, **kw: spawn([sys.executable, *args], **kw)
def stand_in_image(pid):
    assert pid == {os.getpid()}
    Path("restart-waiting").touch()
    return Path.cwd() / "app-0.2.1" / {EXE!r}
instance._image = stand_in_image
sys.exit(entry['launch'](Path.cwd()))
""")
    digest = hashlib.sha256((feed / info.filename).read_bytes()).hexdigest()
    (feed / "SHA256SUMS.txt").write_text(f"{digest}  {info.filename}\n")
    (layout / "Gilhound.exe").write_text("previous launcher")
    monkeypatch.setattr(updates, "installed", lambda: True)
    exiting = Event()
    monkeypatch.setattr(updater.os, "_exit", lambda status: exiting.set())
    spawn = subprocess.Popen
    restarts = []

    def restart(args: list, **kwargs: object) -> subprocess.Popen:
        child = spawn(args, **kwargs)
        restarts.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", restart)
    pidfile = layout / "window.pid"
    pidfile.write_text(str(os.getpid()))
    with version_lease(layout, "0.2.1"), lock(layout / "use-0.2.1.lock"):
        assert client.post("/api/update/check").json()["latest_version"] == info.version
        job = client.post("/api/update/apply")
        assert job.status_code == 202
        assert exiting.wait(5)
        deadline = time.monotonic() + 5
        while not (layout / "restart-waiting").exists() and time.monotonic() < deadline:
            time.sleep(0.025)
        assert (layout / "restart-waiting").exists()
        assert (layout / "pending.txt").read_text() == info.version
        assert len(restarts) == 1 and restarts[0].poll() is None
    with lock(layout / "window.lock"):
        pidfile.unlink()
    assert restarts[0].wait(10) == 0
    assert (layout / "started").read_text().splitlines() == [f"app-{info.version}"]
    assert (layout / "current.txt").read_text() == info.version
    assert (layout / "previous.txt").read_text() == "0.2.1"
    assert not (layout / "pending.txt").exists()
