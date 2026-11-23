"""Bounded in-memory progress and results for asynchronous market jobs."""

from collections.abc import Callable
from secrets import token_hex
from threading import Lock
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel

from gilhound.market.workers import Workers, checkpoint
from gilhound.models import RetainerScanResult, ScanResult, SearchResult, SnipeHit

type Result = ScanResult | RetainerScanResult | SearchResult | list[SnipeHit]
type Progress = Callable[[int, int], None]


class Job(BaseModel):
    id: str
    state: Literal["queued", "running", "complete", "failed"] = "queued"
    error: str | None = None
    done: int = 0
    total: int | None = None
    result: Result | None = None


class JobError(RuntimeError):
    """A failure with a message suitable for display in the desktop UI."""


class Jobs:
    def __init__(self) -> None:
        self.pool = Workers(2)
        self.jobs: dict[str, Job] = {}
        self.lock = Lock()

    def get(self, key: str) -> Job:
        with self.lock:
            if key not in self.jobs:
                raise HTTPException(404, "Job not found.")
            return self.jobs[key].model_copy()

    def submit(self, task: Callable[[Progress], Result | None]) -> Job:
        job = Job(id=token_hex(16))

        def progress(done: int, total: int) -> None:
            with self.lock:
                checkpoint()
                job.done, job.total = done, total

        def work() -> None:
            with self.lock:
                checkpoint()
                job.state = "running"
            try:
                result = task(progress)
            except Exception as exc:
                with self.lock:
                    if isinstance(exc, JobError):
                        job.error = str(exc)
                    job.state = "failed"
            else:
                with self.lock:
                    if self.pool.stop.is_set():
                        return
                    job.result = result
                    job.state = "complete"

        with self.lock:
            if self.pool.stop.is_set():
                raise HTTPException(503, "Scan service is closing.")
            if len(self.jobs) >= 24:
                oldest = next(
                    (k for k, v in self.jobs.items() if v.state in {"failed", "complete"}), None
                )
                if oldest is None:
                    raise HTTPException(429, "Scan queue is full.")
                del self.jobs[oldest]
            self.jobs[job.id] = job
            self.pool.submit(work)
            return job.model_copy()

    def close(self) -> None:
        with self.lock:
            self.pool.close()
            for job in self.jobs.values():
                if job.state in {"queued", "running"}:
                    job.state = "failed"
