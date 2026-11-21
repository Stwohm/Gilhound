"""Deliver Discord alerts without exposing request URLs to HTTP client logs."""

import json
import math
import re
import time
from collections.abc import Callable, Iterator
from threading import Event
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from gilhound.market.snipe import discord_embed, embed_length
from gilhound.models import SnipeHit, UserSettings

from .diagnostics import LOG
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
        sample = SnipeHit.model_validate({
            "item": {"id": 1, "name": "Test · Sample ingot", "hq": True},
            "buy_world": "Odin", "buy_dc": "Light", "buy_ppu": 12000, "buy_qty_at_price": 3,
            "home_median_7d": 48500, "home_lowest": 44999, "profit_per_unit": 31200,
            "total_profit_capped": 93600, "sellable_qty": 3, "est_wait_days": None,
            "data_age_h": 14 / 60, "listing_key": "sample", "first_seen_ts": int(time.time()),
            "tier": {"name": "fresh", "max_age_h": 1, "send_discord": True},
            "volume_proof": {"units_per_day": 4.7, "distinct_buyers_7d": 33,
                             "last_sale_age_h": 2.1, "top_buyer_share": 0.1, "days_with_sales": 7},
        })
        embed = discord_embed(sample, settings.home_world, settings.discord_coin_emoji)
        self.send(settings, {"embeds": [embed], "allowed_mentions": {"parse": []}})

    def notify_many(self, settings: UserSettings, hits: list[SnipeHit], history: History) -> int:
        delivered = 0
        if settings.discord_notifier and settings.webhook_url:
            for batch in batches(settings, hits):
                try:
                    delivered += self.deliver_batch(settings, batch, history)
                except DeliveryError:
                    LOG.warning("Discord delivery failed")
        return delivered

    def deliver_batch(
        self, settings: UserSettings, batch: list[tuple[str, dict]], history: History
    ) -> int:
        leases: dict[str, float] = {}
        embeds = []
        urls: set[str] = set()
        delivered = False

        def extend(delay: float) -> None:
            for key, lease in leases.items():
                leases[key] = history.extend(key, lease, delay)

        try:
            for key, embed in batch:
                lease = history.reserve(key, settings.dedup_cooldown_h * 3600)
                if lease is not None:
                    leases[key] = lease
                    # Discord hides subsequent embeds with identical title URLs.
                    url = embed.get("url")
                    if url in urls:
                        embed = {**embed, "url": f"{url}#listing-{len(embeds)}"}
                    elif url:
                        urls.add(url)
                    embeds.append(embed)
            if embeds:
                self.send(settings, {"embeds": embeds, "allowed_mentions": {"parse": []}}, extend)
                delivered = True
            return len(embeds)
        finally:
            for key, lease in leases.items():
                history.release(key, lease, delivered)


def batches(settings: UserSettings, hits: list[SnipeHit]) -> Iterator[list[tuple[str, dict]]]:
    batch: list[tuple[str, dict]] = []
    size = 0
    for hit in hits:
        if not hit.tier.send_discord:
            continue
        embed = discord_embed(hit, settings.home_world, settings.discord_coin_emoji)
        length = embed_length(embed)
        if len(batch) == 10 or size + length > 6000:
            yield batch
            batch = []
            size = 0
        batch.append((hit.listing_key, embed))
        size += length
    if batch:
        yield batch
