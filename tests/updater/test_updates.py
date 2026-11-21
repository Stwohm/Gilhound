import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile

import pytest

import gilhound
from gilhound import updater
from gilhound.updater._layout import EXE, lock, precedence

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
        return popen([sys.executable, *args], **kwargs)

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


@pytest.mark.parametrize("version", ["../0.1.1", "1.2", "01.2.3", "1.2.3-01"])
def test_bad_version(version: str) -> None:
    with pytest.raises(ValueError):
        precedence(version)


def test_update_cycle(layout: Path, feed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    progress = []
    info = release(feed)
    updater.download_and_stage(info, lambda done, total: progress.append((done, total)))
    assert progress[-1][0] == progress[-1][1] > 0
    assert (layout / "current.txt").read_text() == "0.1.0"
    assert (layout / "app-0.1.1/READY").is_file()
    assert (layout / "pending.txt").read_text() == "0.1.1"
    assert launcher.launch(layout) == 0
    old = layout / "app-0.1.0"
    assert old.is_dir() and not (layout / "pending.txt").exists()
    assert (layout / "current.txt").read_text() == "0.1.1"
    with lock(layout / "use-0.1.0.lock", shared=True):
        assert launcher.launch(layout) == 0
        assert old.is_dir()
    assert launcher.launch(layout) == 0
    assert not old.exists()
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
