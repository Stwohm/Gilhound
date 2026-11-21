"""Bounded parallel JSON transport shared by the market clients."""

import json
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from importlib.metadata import version
from threading import Lock
from typing import Any
from urllib.parse import urlsplit

from gilhound.market.budget import HostBudget
from gilhound.market.workers import checkpoint, wait
from gilhound.market.workers import parallel as parallel

USER_AGENT = f"gilhound/{version('gilhound')}"


class RateLimiter:
    def __init__(
        self,
        rate: int = 25,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = wait,
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

    @contextmanager
    def connection(self) -> Iterator[None]:
        self.acquire()
        yield

    def cooldown(self, seconds: float) -> None:
        with self.lock:
            self.next_start = max(self.next_start, self.clock() + seconds)


def retry_after(header: str | None, fallback: float) -> float:
    if header:
        try:
            value = header.strip()
            if value.isascii() and value.isdigit():
                return float(int(value))
            return max(0, parsedate_to_datetime(value).timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            pass
    return fallback


class HttpClient:
    def __init__(
        self,
        ttl: float = 60,
        *,
        clock: Callable[[], float] = time.monotonic,
        limiter: RateLimiter | None = None,
        timeout: float = 15,
        attempts: int = 4,
        sleep: Callable[[float], None] = wait,
    ) -> None:
        self.timeout = timeout
        self.attempts = attempts
        self.ttl = ttl
        self.clock = clock
        self.limiter = limiter
        self.sleep = sleep
        self.cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self.lock = Lock()

    def request(self, url: str, body: dict[str, Any] | None = None) -> Any:
        checkpoint()
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
        budget = self.limiter or HostBudget(urlsplit(url).hostname or "")
        for attempt in range(self.attempts):
            checkpoint()
            delay = min(30, 2 * (attempt + 1))
            try:
                with budget.connection():
                    checkpoint()
                    try:
                        with urllib.request.urlopen(req, timeout=self.timeout) as response:
                            payload = json.load(response)
                    except urllib.error.HTTPError as exc:
                        if exc.code == 429:
                            budget.cooldown(retry_after(exc.headers.get("Retry-After"), delay))
                        exc.close()
                        raise
                    checkpoint()
                break
            except (OSError, HTTPException, json.JSONDecodeError) as exc:
                if (
                    isinstance(exc, urllib.error.HTTPError)
                    and exc.code not in {408, 429}
                    and exc.code < 500
                ) or attempt + 1 == self.attempts:
                    raise
                if not isinstance(exc, urllib.error.HTTPError) or exc.code != 429:
                    self.sleep(delay)
        if body is None and self.ttl > 0:
            with self.lock:
                self.cache[url] = (self.clock() + self.ttl, payload)
                self.cache.move_to_end(url)
                if len(self.cache) > 256:
                    self.cache.popitem(last=False)
        return payload
