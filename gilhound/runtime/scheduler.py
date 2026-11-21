"""One periodic worker, with interruptible waits and a join on shutdown."""

import threading
import time
from collections.abc import Callable
from concurrent.futures import CancelledError

from gilhound.market.workers import stop_events

from .diagnostics import LOG


class Scheduler:
    def __init__(self, interval: Callable[[], float | None], task: Callable[[], object]) -> None:
        self.cancel = threading.Event()
        self.interval = interval
        self.task = task
        self.condition = threading.Condition()
        self.stopped = True
        self.delay: float | None = None
        self.next_run: float | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        with self.condition:
            if not self.stopped or (self.thread and self.thread.is_alive()):
                return
            self.cancel = threading.Event()
            self.stopped = False
            self.delay = self.interval()
            self.next_run = None if self.delay is None else time.time() + self.delay
            self.thread = threading.Thread(target=self._run, name="snipe-scheduler", daemon=True)
            self.thread.start()

    def configure(self) -> None:
        with self.condition:
            delay = self.interval()
            if self.stopped or delay == self.delay:
                return
            self.next_run = None if delay is None else time.time() + (delay if self.delay else 0)
            self.delay = delay
            self.condition.notify_all()

    def _run(self) -> None:
        stop_events.set((self.cancel,))
        while True:
            with self.condition:
                if self.stopped:
                    return
                remaining = None if self.next_run is None else self.next_run - time.time()
                if remaining is None or remaining > 0:
                    self.condition.wait(remaining)
                    continue
                self.next_run = None
            try:
                self.task()
            except CancelledError:
                return
            except Exception:
                LOG.warning("Snipe scan failed")
            with self.condition:
                if self.next_run is None and self.delay is not None:
                    self.next_run = time.time() + self.delay

    def stop(self) -> None:
        with self.condition:
            self.cancel.set()
            self.stopped = True
            self.next_run = None
            self.condition.notify_all()
        if self.thread:
            self.thread.join(timeout=0.5)
