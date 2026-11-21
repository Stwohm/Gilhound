"""Bounded in-memory market jobs. Retainer queries expire with the job."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from secrets import token_hex
from threading import Lock
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel

from gilhound.models import RetainerScanResult, ScanResult, SnipeHit

type Result = ScanResult | RetainerScanResult | list[SnipeHit]
type Progress = Callable[[int, int], None]


class Job(BaseModel):
    id: str
    state: Literal["queued", "running", "complete", "failed"] = "queued"
    done: int = 0
    total: int | None = None
    result: Result | None = None


class Jobs:
    def __init__(self) -> None:
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.jobs: dict[str, Job] = {}
        self.lock = Lock()

    def get(self, key: str) -> Job:
        with self.lock:
            if key not in self.jobs:
                raise HTTPException(404, "Job not found.")
            return self.jobs[key].model_copy()

    def submit(self, task: Callable[[Progress], Result | None]) -> Job:
        job = Job(id=token_hex(16))
        with self.lock:
            if len(self.jobs) >= 24:
                oldest = next(
                    (k for k, v in self.jobs.items() if v.state in {"failed", "complete"}), None
                )
                if oldest is None:
                    raise HTTPException(429, "Scan queue is full.")
                del self.jobs[oldest]
            self.jobs[job.id] = job

        def progress(done: int, total: int) -> None:
            with self.lock:
                job.done, job.total = done, total

        def work() -> None:
            with self.lock:
                job.state = "running"
            try:
                result = task(progress)
            except Exception:
                with self.lock:
                    job.state = "failed"
            else:
                with self.lock:
                    job.result = result
                    job.state = "complete"

        self.pool.submit(work)
        return self.get(job.id)

    def close(self) -> None:
        self.pool.shutdown(wait=True, cancel_futures=True)
