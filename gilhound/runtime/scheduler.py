"""One periodic worker, with interruptible waits and a join on shutdown."""

import threading
from collections.abc import Callable

from .diagnostics import LOG


class Scheduler:
    def __init__(self, interval: Callable[[], float], task: Callable[[], object]) -> None:
        self.interval = interval
        self.task = task
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.wake.clear()
        self.thread = threading.Thread(target=self._run, name="snipe-scheduler", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        while not self.stop_event.is_set():
            if self.wake.wait(self.interval()):
                self.wake.clear()
                continue
            try:
                self.task()
            except Exception:
                LOG.warning("Snipe scan failed")

    def stop(self) -> None:
        self.stop_event.set()
        self.wake.set()
        if self.thread:
            self.thread.join()
