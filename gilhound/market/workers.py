"""Daemon workers with cooperative cancellation for market I/O."""

import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import CancelledError, Future
from concurrent.futures import wait as wait_futures
from contextvars import ContextVar, copy_context
from itertools import islice
from queue import Queue
from threading import Event, Lock, Thread

stop_events: ContextVar[tuple[Event, ...]] = ContextVar("market_stops", default=())


def checkpoint() -> None:
    if any(event.is_set() for event in stop_events.get()):
        raise CancelledError


def wait(seconds: float) -> None:
    events = stop_events.get()
    if not events:
        time.sleep(seconds)
        return
    deadline = time.monotonic() + seconds
    while True:
        checkpoint()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        events[-1].wait(min(remaining, 0.05))


def result_or_cancel[T](future: Future[T]) -> T:
    while not future.done():
        checkpoint()
        wait_futures([future], timeout=0.05)
    checkpoint()
    return future.result()


class Workers:
    """A small FIFO pool. In-flight system I/O cannot hold interpreter exit open."""

    def __init__(self, size: int) -> None:
        self.stop = Event()
        self.queue: Queue[Callable[[], None] | None] = Queue()
        self.lock = Lock()
        self.threads: list[Thread] = []
        self.size = size

    def _run(self) -> None:
        while (task := self.queue.get()) is not None:
            task()

    def submit[T](self, function: Callable[[], T]) -> Future[T]:
        future: Future[T] = Future()
        context = copy_context()
        context.run(stop_events.set, (*stop_events.get(), self.stop))

        def invoke() -> None:
            if not future.set_running_or_notify_cancel():
                return
            try:
                checkpoint()
                result = function()
                checkpoint()
            except BaseException as exc:
                future.set_exception(exc)
            else:
                future.set_result(result)

        with self.lock:
            if self.stop.is_set():
                raise CancelledError
            if len(self.threads) < self.size:
                worker = Thread(target=self._run, daemon=True)
                worker.start()
                self.threads.append(worker)
            self.queue.put(lambda: context.run(invoke))
        return future

    def close(self) -> None:
        with self.lock:
            if self.stop.is_set():
                return
            self.stop.set()
            for _ in self.threads:
                self.queue.put(None)


def parallel[T, R](function: Callable[[T], R], values: Iterable[T]) -> Iterator[R]:
    pool = Workers(8)
    remaining = iter(values)

    def submit(value: T) -> Future[R]:
        checkpoint()
        return pool.submit(lambda: function(value))

    try:
        pending = deque(submit(value) for value in islice(remaining, 8))
        while pending:
            future = pending.popleft()
            yield result_or_cancel(future)
            for value in remaining:
                pending.append(submit(value))
                break
    finally:
        pool.close()
