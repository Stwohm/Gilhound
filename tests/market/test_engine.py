import io
import urllib.request

import pytest

from gilhound.market.http import HttpClient, RateLimiter
from gilhound.market.profit import profit_per_unit, sellable_qty, wait_days
from gilhound.market.snipe import Deduper, MarketRow, MemoryStore, discord_embed, evaluate
from gilhound.market.universalis import Universalis
from gilhound.market.volume import DAY, volume_proof
from gilhound.models import FreshnessTier, ItemRef, Listing, Sale, SnipeRule


def test_limiter() -> None:
    now = [0.0]

    def advance(seconds: float) -> None:
        now[0] += seconds

    limiter = RateLimiter(clock=lambda: now[0], sleep=advance)
    for _ in range(26):
        limiter.acquire()
    assert now[0] == pytest.approx(1)


def test_retry_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, waits = [], []
    now = [0.0]

    def respond(req: urllib.request.Request, *, timeout: int) -> io.BytesIO:
        calls.append(req)
        if len(calls) < 4:
            raise TimeoutError
        return io.BytesIO(b'{"ok":true}')

    monkeypatch.setattr(urllib.request, "urlopen", respond)
    client = HttpClient(
        clock=lambda: now[0],
        sleep=waits.append,
        limiter=RateLimiter(clock=lambda: 0, sleep=lambda _: None),
    )
    for t in (0, 59, 60):
        now[0] = t
        assert client.request("https://example.invalid") == {"ok": True}
    assert len(calls) == 5 and waits == [2, 4, 6]
    assert calls[0].get_header("User-agent").startswith("gilhound/")


def test_timestamps(monkeypatch: pytest.MonkeyPatch) -> None:
    ts = 1800000000
    raw = {
        "lastUploadTime": ts * 1000 + 999,
        "worldUploadTimes": {"1": ts * 1000},
        "recentHistory": [{"pricePerUnit": 2, "quantity": 1, "hq": True, "timestamp": ts}],
        "listings": [
            {
                "pricePerUnit": 1,
                "quantity": 1,
                "hq": True,
                "retainerName": "example",
                "lastReviewTime": ts * 1000,
            }
        ],
    }
    client = HttpClient()
    monkeypatch.setattr(client, "request", lambda url: raw)
    snap = Universalis(client).current("World", [1])[1]
    assert ts == snap.upload_ts == snap.world_upload_ts["1"] == snap.sales[0].ts
    listing = snap.listings[0]
    assert listing.last_review_ts == ts and listing.retainer_name == "example"
    assert "example" not in listing.model_dump_json()


def sold(ts: int, qty: int = 3, buyer: str = "a") -> Sale:
    return Sale(ppu=2, hq=False, qty=qty, ts=ts, buyer_hash=buyer)


def test_proof() -> None:
    now = DAY * 10
    empty = volume_proof([], now)
    assert empty.last_sale_age_h is None and empty.top_buyer_share == empty.units_per_day == 0
    proof = volume_proof(
        [sold(now - 1, 9), sold(now - DAY, 1, "b"), sold(now - 7 * DAY), sold(now + 1)], now
    )
    assert proof.top_buyer_share == 0.9 and proof.units_per_day == 10 / 7
    assert proof.days_with_sales == proof.distinct_buyers_7d == 2
    assert proof.last_sale_age_h == 1 / 3600


def test_profit() -> None:
    assert profit_per_unit(100, 40, buyer_tax=0.05, seller_tax=0.05, travel_cost=12, qty=4) == 50
    assert sellable_qty(50, 2.5, 3) == 7 and sellable_qty(1, 2.5, 3) == 1
    assert wait_days(5, 3, 2) == 4 and wait_days(0, 1, 0) is None


def test_evaluation_and_cooldown() -> None:
    now = DAY
    item = ItemRef(id=1, name="Item", hq=False)
    row = MarketRow(
        listing=Listing(
            world="World", ppu=2, qty=3, hq=False, retainer_hash="a", last_review_ts=now
        ),
        item=item,
        buy_world="World",
        buy_dc="DC",
        buy_ppu=2,
        buy_qty_at_price=3,
        home_median_7d=10,
        home_lowest=12,
        sellable_qty=1,
        profit_per_unit=7,
        total_profit_capped=7,
        est_wait_days=1,
        volume_proof=volume_proof([sold(now)], now),
        data_age_h=0,
    )
    rule = SnipeRule(min_distinct_buyers_7d=1, min_profit=1, max_top_buyer_share=1)
    tiers = [
        FreshnessTier(name="fresh", max_age_h=1, send_discord=True),
        FreshnessTier(name="stale", max_age_h=6, send_discord=False),
    ]
    (hit,) = evaluate([row], rule, tiers, now)
    (stale,) = evaluate([row], rule, tiers, now + 3600)
    assert stale.tier == tiers[1] and stale.listing_key == hit.listing_key
    assert not evaluate([row], rule, tiers, now + 6 * 3600)
    assert not evaluate([row], rule.model_copy(update={"max_top_buyer_share": 0.5}), tiers, now)
    row.listing.retainer_hash = "b"
    assert evaluate([row], rule, tiers, now)[0].listing_key != hit.listing_key
    store = MemoryStore()
    assert Deduper(store=store).claim(hit, now)
    assert not Deduper(store=store).claim(hit, now + 1)
    assert Deduper(store=store).claim(hit, now + 6 * 3600)
    embed = discord_embed(stale, "Home", "🪙")
    assert embed["fields"][-1]["value"] == "60 min (stale)"
    assert "/li World market" in embed["description"]
