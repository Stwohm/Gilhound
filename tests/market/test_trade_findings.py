import pytest

from gilhound.market import presets
from gilhound.market.snipe import evaluate
from gilhound.market.universalis import Snapshot, Universalis
from gilhound.models import FreshnessTier, ItemRef, Listing, Sale, ScanParams, SnipeRule

NOW = 2_000_000


def offer(price: int, age: int = 0) -> Listing:
    return Listing(
        world="Away", ppu=price, qty=100, hq=False, retainer_hash="seller", last_review_ts=NOW - age
    )


def market(monkeypatch: pytest.MonkeyPatch, offers: dict[int, list[Listing]]) -> None:
    items = [ItemRef(id=i, name=f"Item {i}", hq=False) for i in offers]
    monkeypatch.setattr(presets, "candidates", lambda *_: items)

    def current(self, location, ids, **kwargs):
        listings = [] if location == "Home" else offers[ids[0]]
        return {ids[0]: Snapshot(listings, [], NOW, {})}

    monkeypatch.setattr(Universalis, "current", current)
    monkeypatch.setattr(
        Universalis,
        "history",
        lambda *args, **kw: [Sale(ts=NOW, qty=7, ppu=200, hq=False, buyer_hash="buyer")],
    )


@pytest.mark.parametrize(
    ("cost", "days", "minimum", "eligible"),
    [(300, 3, 0, False), (60, 3, 66, False), (60, 3, 65, True), (0, 0.5, 0, False)],
)
def test_capped_trip_and_wait(
    monkeypatch: pytest.MonkeyPatch, cost: int, days: float, minimum: int, eligible: bool
) -> None:
    market(monkeypatch, {1: [offer(100)]})
    params = ScanParams(
        home_world="Home",
        buy_region="Region",
        seller_tax=0.05,
        sellable_days=days,
        travel_cost={"DC": cost},
        min_profit=minimum,
        min_history_sales=1,
    )
    rows = presets.run_scan(params, clock=lambda: NOW, world_dcs={"Away": "DC"}).rows
    assert bool(rows) == eligible
    if eligible:
        (row,) = rows
        assert row.sellable_qty == row.days_to_sell == row.est_wait_days == 3
        assert (row.profit_per_unit, row.total_profit_capped, row.capital_needed) == (65, 195, 315)
        assert row.roi_pct == pytest.approx(195 / 315 * 100)


@pytest.mark.parametrize("limit", [1, 2, None])
def test_snipe_tiers_then_dedup_then_limit(
    monkeypatch: pytest.MonkeyPatch, limit: int | None
) -> None:

    market(
        monkeypatch, {1: [offer(45, 86400)], 2: [offer(50, 86400), offer(60), offer(60), offer(70)]}
    )
    tiers = [FreshnessTier(name="fresh", max_age_h=1, send_discord=True)]
    rule = SnipeRule(min_profit=1, min_distinct_buyers_7d=1, max_top_buyer_share=1)
    params = ScanParams(
        home_world="Home",
        buy_region="Region",
        preset="snipe",
        seller_tax=0.05,
        min_history_sales=1,
        result_limit=limit,
        snipe_rule=rule,
        freshness_tiers=tiers,
    )
    result = presets.run_scan(params, clock=lambda: NOW, world_dcs={})
    hits = evaluate(result.rows, rule, tiers, NOW)
    assert [hit.buy_ppu for hit in hits] == [60, 70][:limit]


def test_rank_charges_travel_before_limiting(monkeypatch: pytest.MonkeyPatch) -> None:
    local = offer(110).model_copy(update={"world": "Local"})
    market(monkeypatch, {1: [offer(100)], 2: [local]})
    params = ScanParams(
        home_world="Home",
        buy_region="Region",
        seller_tax=0.05,
        min_history_sales=1,
        travel_cost={"Remote": 60},
        result_limit=1,
    )
    result = presets.run_scan(params, clock=lambda: NOW, world_dcs={"Away": "Remote"})
    assert [row.item.id for row in result.rows] == [2]
