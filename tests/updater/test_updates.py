import hashlib
import importlib.util
import json
import subprocess
import sys
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
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
raise SystemExit({})
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
def feed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "feed"
    path.mkdir()
    handler = partial(SimpleHTTPRequestHandler, directory=str(path))
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        base = f"http://127.0.0.1:{server.server_port}"
        names = ["Gilhound-0.1.1-win64.zip", "SHA256SUMS.txt"]
        assets = [dict(name=name, browser_download_url=f"{base}/{name}") for name in names]
        (path / "latest").write_text(json.dumps(dict(tag_name="v0.1.1", assets=assets)))
        monkeypatch.setenv("GILHOUND_UPDATE_FEED", base + "/latest")
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02})
        thread.start()
        try:
            yield path
        finally:
            server.shutdown()
            thread.join()


def release(feed: Path, status: int = 0, bad_member: bool = False) -> updater.UpdateInfo:
    info = updater.check()
    assert info is not None and info.version == "0.1.1"
    path = feed / info.filename
    with ZipFile(path, "w") as archive:
        archive.writestr("Gilhound.exe", "launcher")
        archive.writestr("install.ps1", "")
        archive.writestr("app-0.1.1/READY", "")
        archive.writestr(f"app-0.1.1/{EXE}", APP.format(status))
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
    updater.download_and_stage(release(feed, status=5))
    assert launcher.launch(layout) == 0
    assert "Rolling back 0.1.1 to 0.1.0" in caplog.text
    assert (layout / "current.txt").read_text() == "0.1.0"
    assert (layout / "started").read_text().splitlines() == ["app-0.1.1", "app-0.1.0"]
    assert not (layout / "pending.txt").exists()


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
    launcher.install(source, root)
    assert shortcuts == [root, root]
    assert (root / "current.txt").read_text() == "0.1.0"
    assert (root / folder.name / "READY").is_file()
    assert (root / folder.name / EXE).read_bytes() == b"desktop"
    assert (root / "Gilhound.exe").read_bytes() == b"launcher"

