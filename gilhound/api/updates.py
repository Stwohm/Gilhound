"""Installed release checks and update jobs for the desktop API."""

import os
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from fastapi import HTTPException

from gilhound import __version__, updater
from gilhound.api.jobs import Job, Jobs, Progress
from gilhound.runtime.diagnostics import LOG
from gilhound.updater._layout import EXE, install_root, ready


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


def _check() -> updater.UpdateInfo | None:
    try:
        return updater.check()
    except Exception:
        LOG.warning("Update check failed")
        return None


class Updates:
    def __init__(self) -> None:
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.check: Future[updater.UpdateInfo | None] | None = None
        self.expires = 0.0
        self.lock = Lock()
        self.applying = Lock()

    def latest(self) -> updater.UpdateInfo | None:
        if not installed():
            return None
        with self.lock:
            now = time.monotonic()
            if self.check is None or (self.check.done() and now >= self.expires):
                self.check = self.pool.submit(_check)
                self.expires = now + 3600
            return self.check.result() if self.check.done() else None

    def apply(self, jobs: Jobs) -> Job:
        release = self.latest()
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
