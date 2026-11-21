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
        assert row.ask_exit_ppu is row.ask_profit_capped is row.ask_below_floor is None
        assert row.buy_stacks == [100] and row.whole_stack_outlay == 10500
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


def test_safe_flip_rejections_and_stale_quote(monkeypatch: pytest.MonkeyPatch) -> None:
    market(monkeypatch, {1: [offer(100, 3600)]})
    params = ScanParams(home_world="Home", buy_region="Region", preset="safe_flip", seller_tax=0.05,
                        min_profit=1, min_history_sales=1)
    assert (params.min_units_day, params.min_distinct_buyers, params.max_top_buyer_share,
            params.max_sale_age_h, params.max_data_age_h) == (2.5, 8, 0.5, 24, 1)
    rejected = presets.run_scan(params, clock=lambda: NOW, world_dcs={}).rejected
    assert {"Distinct buyers", "Units/day", "Top buyer share"} <= set(rejected[0].reasons)
    params.min_units_day = params.min_distinct_buyers = params.min_days_with_sales = 0
    params.max_top_buyer_share = 1
    result = presets.run_scan(params, clock=lambda: NOW, world_dcs={})
    assert result.rows == [] and result.rejected[0].reasons == ["Buy quote age"]


def test_travel_recheck_uses_world_and_home_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from gilhound.market.recheck import recheck
    from gilhound.models import QuoteRequest

    market(monkeypatch, {1: [offer(51, 3600), offer(51, 7200), offer(52)]})
    q = QuoteRequest(item_id=1, hq=False, buy_world="Away", home_world="Home")
    data = recheck(q, clock=lambda: NOW)
    assert (data.price, data.qty, data.stacks, data.age_h) == (51, 200, [100, 100], 2)
    assert data.home_lowest is None and data.home_median == 200
    market(monkeypatch, {1: []})
    empty = recheck(q, clock=lambda: NOW)
    assert empty.price is None and empty.qty == 0 and empty.age_h is None


def test_safe_flip_chooses_eligible_stacks(monkeypatch: pytest.MonkeyPatch) -> None:
    expensive_trip = offer(55).model_copy(update={"world": "Remote"})
    market(monkeypatch, {1: [offer(50, 7200), expensive_trip, offer(60, 7200), offer(60)]})
    params = ScanParams(home_world="Home", buy_region="Region", preset="safe_flip",
                        min_history_sales=1, min_profit=1, min_units_day=0,
                        min_distinct_buyers=1, min_days_with_sales=1, max_top_buyer_share=1,
                        seller_tax=0.05, travel_cost={"Light": 10000})
    rows = presets.run_scan(params, clock=lambda: NOW, world_dcs={"Remote": "Light"}).rows
    assert [(r.buy_ppu, r.buy_stacks, r.data_age_h) for r in rows] == [(60, [100], 0)]
