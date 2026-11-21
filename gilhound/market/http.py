"""Bounded parallel JSON transport shared by the market clients."""

import json
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPException
from importlib.metadata import version
from threading import BoundedSemaphore, Lock
from typing import Any

USER_AGENT = f"gilhound/{version('gilhound')}"


class RateLimiter:
    def __init__(
        self,
        rate: int = 25,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.interval = 1 / rate
        self.clock = clock
        self.sleep = sleep
        self.lock = Lock()
        self.next_start = 0.0

    def acquire(self) -> None:
        with self.lock:
            delay = self.next_start - self.clock()
            if delay > 0:
                self.sleep(delay)
            self.next_start = self.clock() + self.interval


_LIMITER = RateLimiter()
_CONNECTIONS = BoundedSemaphore(8)


def parallel[T, R](function: Callable[[T], R], values: Iterable[T]) -> Iterator[R]:
    with ThreadPoolExecutor(max_workers=8) as pool:
        yield from pool.map(function, values)


class HttpClient:
    def __init__(
        self,
        ttl: float = 60,
        *,
        clock: Callable[[], float] = time.monotonic,
        limiter: RateLimiter = _LIMITER,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.ttl = ttl
        self.clock = clock
        self.limiter = limiter
        self.sleep = sleep
        self.cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self.lock = Lock()

    def request(self, url: str, body: dict[str, Any] | None = None) -> Any:
        if body is None:
            with self.lock:
                cached = self.cache.get(url)
                if cached and self.clock() < cached[0]:
                    return cached[1]
        req = urllib.request.Request(
            url,
            data=None if body is None else json.dumps(body).encode(),
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        )
        for attempt in range(4):
            try:
                with _CONNECTIONS:
                    self.limiter.acquire()
                    with urllib.request.urlopen(req, timeout=120) as response:
                        payload = json.load(response)
                break
            except (OSError, HTTPException, json.JSONDecodeError) as exc:
                if (
                    isinstance(exc, urllib.error.HTTPError)
                    and exc.code not in {408, 429}
                    and exc.code < 500
                ) or attempt == 3:
                    raise
                self.sleep(2 * (attempt + 1))
        if body is None and self.ttl > 0:
            with self.lock:
                self.cache[url] = (self.clock() + self.ttl, payload)
                self.cache.move_to_end(url)
                if len(self.cache) > 256:
                    self.cache.popitem(last=False)
        return payload
