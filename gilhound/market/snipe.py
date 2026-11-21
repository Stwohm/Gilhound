import hashlib
import json
from collections.abc import Iterable
from threading import Lock
from typing import Protocol

from pydantic import Field

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


def discord_payload(hit: SnipeHit) -> dict[str, object]:
    quality = "HQ" if hit.item.hq else "NQ"
    return {
        "allowed_mentions": {"parse": []},
        "content": (
            f"{hit.item.name} ({quality}) on {hit.buy_world}\n"
            f"{hit.buy_ppu:,} gil × {hit.buy_qty_at_price:,} | "
            f"home median {hit.home_median_7d:,.0f}\n"
            f"Profit {hit.profit_per_unit:,.0f} gil/unit | data age {hit.data_age_h:.2f} h\n"
            f"/li {hit.buy_world} market"
        ),
    }
