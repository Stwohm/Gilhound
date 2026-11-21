"""Deliver Discord alerts without exposing request URLs to HTTP client logs."""

import json
import math
import re
from collections.abc import Callable
from threading import Event
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from gilhound.market.snipe import discord_payload
from gilhound.models import SnipeHit, UserSettings

from .store import History


class DeliveryError(Exception):
    """Safe for presentation to the local user."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: object) -> None:
        return None


def post(url: str, payload: dict[str, object]) -> tuple[int, bytes]:
    request = Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Gilhound"},
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=10) as response:
            return response.status, b""
    except HTTPError as exc:
        with exc:
            return exc.code, exc.read(4096)


class Discord:
    def __init__(
        self,
        transport: Callable[[str, dict[str, object]], tuple[int, bytes]] = post,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.transport = transport
        self.stopped = Event()
        self.sleep = sleep or self.stopped.wait

    def send(
        self,
        settings: UserSettings,
        payload: dict[str, object],
        extend: Callable[[float], None] | None = None,
    ) -> None:
        url = settings.webhook_url.get_secret_value() if settings.webhook_url else ""
        if not re.fullmatch(r"https://discord(?:app)?\.com/api/webhooks/\d+/[\w.-]+", url):
            raise DeliveryError("Configure a valid Discord webhook in Settings.")
        try:
            for attempt in range(3):
                if self.stopped.is_set():
                    break
                status, body = self.transport(url, payload)
                if 200 <= status < 300:
                    return
                if status != 429 or attempt == 2:
                    break
                delay = float(json.loads(body)["retry_after"])
                if not math.isfinite(delay) or delay < 0:
                    break
                if extend:
                    extend(delay)
                self.sleep(delay)
        except (OSError, ValueError, TypeError, KeyError):
            pass
        raise DeliveryError("Discord delivery failed. Check the webhook and try again later.")

    def test(self, settings: UserSettings) -> None:
        self.send(
            settings, {"content": "Gilhound test message.", "allowed_mentions": {"parse": []}}
        )

    def notify(self, settings: UserSettings, hit: SnipeHit, history: History) -> None:
        if not settings.discord_notifier or not hit.tier.send_discord:
            return
        lease = history.reserve(hit.listing_key, settings.dedup_cooldown_h * 3600)
        if lease is None:
            return
        delivered = False

        def extend(delay: float) -> None:
            nonlocal lease
            lease = history.extend(hit.listing_key, lease, delay)

        try:
            self.send(settings, discord_payload(hit), extend)
            delivered = True
        finally:
            history.release(hit.listing_key, lease, delivered)
