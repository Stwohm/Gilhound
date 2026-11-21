"""GitHub Releases updater. Progress callbacks receive downloaded and total bytes."""

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from http.client import HTTPResponse
from pathlib import Path
from tempfile import TemporaryFile
from typing import NoReturn
from urllib.parse import urlsplit
from urllib.request import urlopen
from zipfile import ZipFile

import gilhound
from gilhound.updater._layout import (
    EXE,
    install_root,
    lock,
    pointer,
    precedence,
    ready,
    rejected,
    replace_launcher,
    write_pointer,
)

from .motw import unblock

FEED = "https://api.github.com/repos/Stwohm/Gilhound/releases/latest"


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    asset_url: str
    checksum_url: str

    @property
    def filename(self) -> str:
        return f"Gilhound-{self.version}-win64.zip"


def _open(url: str) -> HTTPResponse:
    parsed = urlsplit(url)
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    ):
        raise ValueError("Expected HTTPS or a local test feed")
    return urlopen(url, timeout=30)


def check() -> UpdateInfo | None:
    with _open(os.environ.get("GILHOUND_UPDATE_FEED", FEED)) as response:
        release = json.load(response)
    version = release["tag_name"].removeprefix("v")
    if precedence(version) <= precedence(gilhound.__version__):
        return None
    if os.environ.get("LOCALAPPDATA") and rejected(install_root(), version):
        return None
    assets = {item["name"]: item["browser_download_url"] for item in release["assets"]}
    return UpdateInfo(version, assets[f"Gilhound-{version}-win64.zip"], assets["SHA256SUMS.txt"])


def _extract(archive: ZipFile, folder: Path, version: str) -> None:
    prefix = f"app-{version}/"
    for entry in archive.infolist():
        if not entry.filename.startswith(prefix):
            continue
        relative = entry.filename[len(prefix):].removesuffix("/")
        if not relative or relative.upper() == "READY":
            continue
        parts = relative.split("/")
        if any(not _safe_component(part) for part in parts):
            raise ValueError("Invalid release archive path")
        target = folder.joinpath(*parts)
        if entry.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
    if not (folder / EXE).is_file():
        raise ValueError("Release archive is missing the application")


def _safe_component(part: str) -> bool:
    device = part.split(".", 1)[0].rstrip().upper()
    return bool(
        part and not part.endswith((" ", "."))
        and not re.search(r'[<>:"/\\|?*\x00-\x1f]', part)
        and not re.fullmatch(r"CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[0-9¹²³]", device)
    )


def download_and_stage(
    release: UpdateInfo, progress: Callable[[int, int], None] | None = None
) -> None:
    """Stage a verified release. A total of zero means the feed omitted its length."""
    version = release.version
    precedence(version)
    root = install_root()
    folder = root / f"app-{version}"
    partial = root / f"app-{version}.partial"
    with _open(release.checksum_url) as response:
        sums = response.read().decode("ascii")
    pattern = rf"^([a-fA-F0-9]{{64}}) [ *]{re.escape(release.filename)}\r?$"
    matches = re.findall(pattern, sums, re.M)
    if len(matches) != 1:
        raise ValueError("Missing or ambiguous SHA256SUMS entry")
    with TemporaryFile(dir=root) as temp:
        digest = hashlib.sha256()
        with _open(release.asset_url) as response:
            total = int(response.headers.get("Content-Length", 0))
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                temp.write(chunk)
                if progress:
                    progress(temp.tell(), total)
        if digest.hexdigest() != matches[0].lower():
            raise ValueError("Release SHA256 mismatch")
        with lock(root / "install.lock"):
            if rejected(root, version):
                raise ValueError("Release previously failed startup")
            current = pointer(root, "current.txt")
            pending = pointer(root, "pending.txt")
            if any(v and precedence(v) > precedence(version) for v in [current, pending]):
                raise ValueError("A newer version is already installed or staged")
            if current == version:
                return
            if not ready(folder):
                if ready(partial):
                    raise ValueError("Staging path contains an installed release")
                # Both names are inactive under the installation lock.
                for stale in (partial, folder):
                    if stale.exists():
                        shutil.rmtree(stale)
                partial.mkdir()
                try:
                    with ZipFile(temp) as archive:
                        _extract(archive, partial, version)
                    unblock(partial)
                    partial.rename(folder)
                    (folder / "READY").touch()
                finally:
                    if partial.exists():
                        shutil.rmtree(partial)
            bundled_launcher = folder / "Gilhound.exe"
            if bundled_launcher.is_file():
                replace_launcher(bundled_launcher, root)
            write_pointer(root, "pending.txt", version)


download = download_and_stage


def apply_and_restart() -> NoReturn:
    root = install_root()
    version = pointer(root, "pending.txt")
    if not version or not ready(root / f"app-{version}"):
        raise RuntimeError("No complete update is staged")
    env = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1", "GILHOUND_RESTART_VERSION": version}
    subprocess.Popen([root / "Gilhound.exe"], cwd=root, env=env)
    # F2 may call from an API worker thread. SystemExit only ends that thread.
    os._exit(0)

