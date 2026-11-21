"""Installed release checks and update jobs for the desktop API."""

import os
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from math import ceil
from pathlib import Path
from threading import Lock

from fastapi import HTTPException

from gilhound import __version__, updater
from gilhound.api.jobs import Job, Jobs, Progress
from gilhound.models import AppInfo
from gilhound.runtime.diagnostics import LOG
from gilhound.updater._layout import EXE, install_root, ready

CHECK_COOLDOWN = 10
CHECK_CACHE_TTL = 3600
DEV_MODE = "Updates unavailable in dev mode."
FEED_ERROR = "Could not check for updates. The release feed is unavailable. Please try again."


def installed() -> bool:
    if not getattr(sys, "frozen", False) or not os.environ.get("LOCALAPPDATA"):
        return False
    root = install_root()
    folder = root / f"app-{__version__}"
    return (
        Path(sys.executable).resolve() == (folder / EXE).resolve()
        and ready(folder)
        and (root / "Gilhound.exe").is_file()
    )


class Updates:
    def __init__(self) -> None:
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.pending: Future[AppInfo] | None = None
        self.release: updater.UpdateInfo | None = None
        self.snapshot = AppInfo(version=__version__, update_available=False)
        self.expires = 0.0
        self.retry_at = 0.0
        self.lock = Lock()
        self.applying = Lock()

    def _check(self) -> AppInfo:
        error = None
        release = None
        try:
            release = updater.check()
        except Exception:
            LOG.warning("Update check failed")
            error = FEED_ERROR
        with self.lock:
            if error is None:
                self.release = release
            self.snapshot = AppInfo(
                version=__version__, update_available=self.release is not None,
                latest_version=self.release.version if self.release else None,
                update_checked_at=int(time.time()), update_check_error=error,
            )
            self.expires = time.monotonic() + CHECK_CACHE_TTL
            return self.snapshot

    def info(self) -> AppInfo:
        if not installed():
            return AppInfo(version=__version__, update_available=False, updates_supported=False)
        with self.lock:
            if self.pending is None or (self.pending.done() and time.monotonic() >= self.expires):
                self.pending = self.pool.submit(self._check)
            return self.snapshot

    def refresh(self) -> AppInfo:
        if not installed():
            raise HTTPException(409, DEV_MODE)
        with self.lock:
            now = time.monotonic()
            if now < self.retry_at:
                raise HTTPException(
                    429, "Please wait 10 seconds between update checks.",
                    headers={"Retry-After": str(ceil(self.retry_at - now))},
                )
            self.retry_at = now + CHECK_COOLDOWN
            if self.pending is None or self.pending.done():
                self.pending = self.pool.submit(self._check)
            pending = self.pending
        info = pending.result()
        if info.update_check_error:
            raise HTTPException(502, info.update_check_error)
        return info

    def apply(self, jobs: Jobs) -> Job:
        if not self.info().updates_supported:
            raise HTTPException(409, "No update available.")
        with self.lock:
            release = self.release
        if release is None:
            raise HTTPException(409, "No update available.")
        if not self.applying.acquire(blocking=False):
            raise HTTPException(409, "Update already in progress.")

        def restart(progress: Progress) -> None:
            try:
                updater.download_and_stage(release, progress)
                updater.apply_and_restart()
            except Exception:
                LOG.warning("Update installation failed")
                raise
            finally:
                self.applying.release()

        try:
            return jobs.submit(restart)
        except Exception:
            self.applying.release()
            raise

    def close(self) -> None:
        self.pool.shutdown(cancel_futures=True)
