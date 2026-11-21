import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from gilhound.api.jobs import Jobs
from gilhound.market.http import parallel


def test_close_cancels_active_batch_and_queued_jobs() -> None:
    jobs = Jobs()
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def block(value: int) -> None:
        calls.append(value)
        entered.set()
        release.wait(4)

    def scan(progress):
        try:
            list(parallel(block, range(100)))
        finally:
            finished.set()

    try:
        ids = [jobs.submit(scan).id for _ in range(3)]
        assert entered.wait(2)
        start = time.monotonic()
        jobs.close()
        assert finished.wait(1)
        assert time.monotonic() - start < 1.5
        assert all(jobs.get(key).state == "failed" for key in ids)
    finally:
        release.set()
        jobs.close()
    assert calls and max(calls) < 8


def test_process_exits_with_blocked_market_transport(tmp_path: Path) -> None:
    # A daemon job is insufficient if a nested executor registers an atexit join.
    script = """
import threading
import urllib.request
from gilhound.api.jobs import Jobs
from gilhound.market.http import HttpClient, parallel
entered = threading.Event()
def blocked(*args, **kwargs):
    entered.set()
    threading.Event().wait(15)
    raise TimeoutError
urllib.request.urlopen = blocked
jobs = Jobs()
jobs.submit(lambda progress: list(parallel(HttpClient().request, ["https://example.invalid"])))
assert entered.wait(2)
jobs.close()
"""

    subprocess.run(
        [sys.executable, "-c", script],
        env=os.environ | {"APPDATA": str(tmp_path)},
        timeout=5,
        check=True,
    )
