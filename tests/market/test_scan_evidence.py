from gilhound.market.presets import _opportunities
from gilhound.market.universalis import Snapshot, Universalis
from gilhound.market.volume import DAY
from gilhound.models import ItemRef, Listing, Sale, ScanParams


def test_two_weeks_of_evidence() -> None:
    now = 30 * DAY

    def sale(days: int, qty: int, price: int, hq: bool = False) -> Sale:
        return Sale(ts=now - days * DAY, qty=qty, ppu=price, hq=hq, buyer_hash=str(days))

    def listing(world: str, price: int, qty: int) -> Listing:
        return Listing(
            world=world,
            ppu=price,
            qty=qty,
            hq=False,
            retainer_hash="seller",
            last_review_ts=now - 3600,
        )

    class Market(Universalis):
        def __init__(self) -> None:
            pass

        def current(self, location: str, ids: list[int], **kwargs: object) -> dict[int, Snapshot]:
            listings = (
                [listing("Home", 100, 2), listing("Home", 110, 4)]
                if location == "Home"
                else [listing("World", 40, 8), listing("Other", 44, 3)]
            )
            return {1: Snapshot(listings, [], now, {})}

        def history(self, location: str, item: int, **kwargs: object) -> list[Sale]:
            assert kwargs["within"] == 1209600
            return [
                sale(0, 7, 120),
                sale(6, 7, 100),
                sale(7, 1, 55),
                sale(13, 3, 55),
                sale(14, 50, 8),
                sale(1, 2, 300, True),
            ]

    params = ScanParams(
        home_world="Home", buy_region="Region", seller_tax=0, buyer_tax=0, min_history_sales=2
    )
    found = _opportunities(ItemRef(id=1, name="Item", hq=False), params, Market(), now, {}, False)
    assert found
    row = found[0][1]
    assert row.units_sold_7d == 14 and row.sales_7d == 2 and row.average_sale_size == 7
    assert (row.min_sale_price, row.home_median_7d, row.max_sale_price) == (100, 110, 120)
    assert row.hq_sale_share == 1 / 3
    assert (
        row.home_listing_count,
        row.distinct_sellers,
        row.rival_stock,
        row.units_below_sell,
    ) == (2, 1, 6, 2)
    assert (row.nearby_listings, row.nearby_units, row.second_buy_price, row.next_listing_gap) == (
        2,
        11,
        44,
        4,
    )
    assert row.trend_pct == 100
    assert row.daily_units_14d == [3, 0, 0, 0, 0, 0, 1, 7, 0, 0, 0, 0, 0, 7]
    assert (row.roi_pct, row.capital_needed, row.profit_per_day, row.days_to_sell) == (
        175,
        240,
        140,
        3,
    )

    params.min_profit = 55
    params.travel_cost = {"?": 24}
    params.seller_tax = params.buyer_tax = 0.05
    params.large_buy_gil = 330
    params.min_net_multiple = 2
    _, taxed = _opportunities(row.item, params, Market(), now, {}, False)[0]
    assert taxed.home_lowest_units == 2 and taxed.ask_exit_ppu == 100
    assert taxed.sellable_qty == 6 and taxed.buy_stacks == [8]
    assert taxed.ask_profit_capped == (95 - 42) * 6 - 24
    assert taxed.ask_below_floor and taxed.median_profit_per_unit >= params.min_profit
    assert taxed.whole_stack_outlay == 336 and taxed.large_buy_below_multiple
