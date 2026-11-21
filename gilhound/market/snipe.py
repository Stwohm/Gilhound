import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from threading import Lock
from typing import Protocol

from pydantic import Field

from gilhound import __version__
from gilhound.market.items import market_url
from gilhound.models import FreshnessTier, Listing, ScanRow, SnipeHit, SnipeRule


class MarketRow(ScanRow):
    """Keep source listing identity inside the engine until notification evaluation."""

    listing: Listing = Field(exclude=True, repr=False)


def listing_key(row: ScanRow, listing: Listing) -> str:
    identity = [
        listing.world,
        row.item.id,
        listing.hq,
        listing.ppu,
        listing.qty,
        listing.retainer_hash,
    ]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def evaluate(
    rows: Iterable[ScanRow | tuple[ScanRow, Listing]],
    rule: SnipeRule,
    tiers: list[FreshnessTier],
    now: int,
) -> list[SnipeHit]:
    """Evaluate scan output, or pair a deserialised ScanRow with its source Listing."""
    ordered = sorted(tiers, key=lambda tier: tier.max_age_h)
    hits = []
    for source in rows:
        if isinstance(source, tuple):
            row, listing = source
        elif isinstance(source, MarketRow):
            row, listing = source, source.listing
        else:
            raise ValueError("A deserialised scan row requires its source Listing")
        proof = row.volume_proof
        if not listing.last_review_ts:
            continue
        age = max(0, now - listing.last_review_ts) / 3600
        tier = next((tier for tier in ordered if age < tier.max_age_h), None)
        if (
            tier is None
            or row.buy_ppu > row.home_median_7d * rule.max_home_median_ratio
            or row.profit_per_unit < rule.min_profit
            or proof.distinct_buyers_7d < rule.min_distinct_buyers_7d
            or proof.last_sale_age_h is None
            or proof.last_sale_age_h + age - row.data_age_h >= rule.max_last_sale_age_h
            or proof.top_buyer_share > rule.max_top_buyer_share
        ):
            continue
        data = row.model_dump()
        data["data_age_h"] = age
        hits.append(
            SnipeHit(**data, tier=tier, first_seen_ts=now, listing_key=listing_key(row, listing))
        )
    return hits


class CooldownStore(Protocol):
    def claim(self, key: str, now: int, cooldown_s: float) -> bool:
        """Atomically record the time if the previous claim has expired."""
        ...


class MemoryStore:
    def __init__(self) -> None:
        self.times: dict[str, int] = {}
        self.lock = Lock()

    def claim(self, key: str, now: int, cooldown_s: float) -> bool:
        with self.lock:
            previous = self.times.get(key)
            if previous is not None and now - previous < cooldown_s:
                return False
            self.times[key] = now
            return True


class Deduper:
    def __init__(self, cooldown_h: float = 6, store: CooldownStore | None = None) -> None:
        self.store = MemoryStore() if store is None else store
        self.cooldown_s = cooldown_h * 3600

    def claim(self, hit: SnipeHit, now: int) -> bool:
        return self.store.claim(hit.listing_key, now, self.cooldown_s)


def clip(value: str, limit: int) -> str:
    value = value.replace("@everyone", "@\u200beveryone").replace("@here", "@\u200bhere")
    return value.encode("utf-16-le")[: limit * 2].decode("utf-16-le", errors="ignore")


def discord_embed(hit: SnipeHit, home: str, coin: str) -> dict:
    proof = hit.volume_proof
    tier = clip(hit.tier.name, 80)
    world = clip(hit.buy_world.replace("`", "").replace("\n", " "), 100)
    last = (
        "last sale unknown"
        if proof.last_sale_age_h is None
        else f"last {proof.last_sale_age_h:.1f} h ago"
    )
    lowest = "Unknown" if hit.home_lowest is None else f"{hit.home_lowest:,} {coin}"
    unit_profit = f"{hit.profit_per_unit:+,.0f} {coin} / unit"
    total_profit = f"{hit.total_profit_capped:+,.0f} {coin} total"
    sales = f"{proof.units_per_day:,.1f}/day · {proof.distinct_buyers_7d:,} buyers"
    fields = [
        ("🛒 Buy", f"{hit.buy_ppu:,} {coin} × {hit.buy_qty_at_price:,}\n{world} ({hit.buy_dc})"),
        (f"📊 {home} median", f"{hit.home_median_7d:,.0f} {coin}"),
        (f"🏷️ Lowest on {home}", lowest),
        ("💰 Profit", f"{unit_profit}\n{total_profit}"),
        ("📈 Sells", f"{sales} · {last}"),
        ("🕒 Data age", f"{hit.data_age_h * 60:,.0f} min ({tier})"),
    ]
    embed = {
        "title": clip(hit.item.name, 253) + (" HQ" if hit.item.hq else ""),
        "description": f"```\n/li {world} market\n```",
        "color": 0x2ECC71 if tier.casefold() == "fresh" else 0xF1A52B,
        "fields": [
            {"name": clip(k, 256), "value": clip(v, 512), "inline": True} for k, v in fields
        ],
        "footer": {"text": f"Gilhound v{__version__} · {tier}"},
        "timestamp": datetime.fromtimestamp(hit.first_seen_ts, UTC).isoformat(),
    }
    url = market_url(hit.item.id, home)
    if len(url) <= 2048:
        embed["url"] = url
    if hit.item.icon_url:
        embed["thumbnail"] = {"url": hit.item.icon_url}
    return embed


def embed_length(embed: dict) -> int:
    parts = [embed["title"], embed["description"], embed["footer"]["text"]]
    for field in embed["fields"]:
        parts.extend((field["name"], field["value"]))
    return sum(len(part.encode("utf-16-le")) // 2 for part in parts)
