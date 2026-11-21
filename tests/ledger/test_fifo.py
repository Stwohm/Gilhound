from pathlib import Path

from gilhound.ingest.common import Records, Source, Stock, event, item_ref
from gilhound.ledger import Acquisition, ItemAdjustment, ManualAdjustments, build_ledger, calculate
from gilhound.models import LedgerEventKind as K
from gilhound.models import SourceName as S
from gilhound.models import SourceStatus


def test_partial_split_quality_unlist() -> None:
    nq, hq = item_ref(12, False), item_ref(12, True)
    rows = [
        event(S.CASHFLOW, K.BUY, nq, 3, 1, 101),
        event(S.CASHFLOW, K.BUY, nq, 1, 2, 20),
        event(S.CASHFLOW, K.BUY, hq, 1, 2, 500),
        event(S.ALLAGAN_MARKET, K.LIST, nq, 3, 3, None),
        event(S.ALLAGAN_MARKET, K.UNLIST, nq, 2, 4, None),
        event(S.ALLAGAN_MARKET, K.LIST, nq, 1, 5, None),
        event(S.CASHFLOW, K.SELL, nq, 2, 6, 115),
    ]
    partial = calculate(rows)
    position, high = partial.positions
    assert (position.qty_open, position.listed_qty, position.avg_cost) == (2, 0, 27)
    assert high.avg_cost == 500
    assert partial.summary.realised_profit == 48
    final = calculate([*rows, event(S.CASHFLOW, K.SELL, nq, 2, 7, 90)])
    assert final.summary.known_cost_of_goods_sold == 121
    assert final.per_item[12, False].realised_profit == 84


def test_unknown_manual_and_personal(tmp_path: Path) -> None:
    item = item_ref(10, True)
    sale = event(S.CASHFLOW, K.SELL, item, 4, 2, 101)
    trade = event(S.CASHFLOW, K.TRADE_IN, item, 1, 1, None)
    manual = ManualAdjustments(acquisitions=[Acquisition(item=item, qty=2, ts=0, ppu=10)])
    unknown = calculate([trade], manual).positions[0]
    assert not unknown.cost_known and unknown.avg_cost is None
    history = [trade, sale]
    mixed = calculate(history, manual).summary
    assert (mixed.unknown_cost_qty_sold, mixed.realised_profit, mixed.sales_proceeds) == (
        2,
        30,
        101,
    )
    manual.items.append(ItemAdjustment(item=item, unknown_unit_cost=5))
    manual.save(tmp_path)
    assert calculate(history, ManualAdjustments.load(tmp_path)).summary.realised_profit == 71
    manual.items[0].personal = True
    manual.save(tmp_path)
    personal = calculate(history, ManualAdjustments.load(tmp_path))
    assert personal.summary.sales_proceeds == personal.summary.realised_profit == 0
    assert personal.positions[0].qty_open == 0


def test_one_to_one_cross_source_sale() -> None:
    item = item_ref(10, True)
    rows = [
        event(s, K.SELL, item, 2, ts, total)
        for s, ts, total in [
            (S.ALLAGAN_MARKET, 400, 205),
            (S.CASHFLOW, 100, 202),
            (S.CASHFLOW, 100, 201),
            (S.RESTOCKY, 100, 202),
            (S.ALLAGAN_MARKET, 401, 200),
        ]
    ]
    result = calculate(rows)
    assert len(result.events) == 3
    assert result.summary.sales_proceeds == 603


def test_snapshot_overlap_and_remaining_cost() -> None:
    item = item_ref(8, False)

    def source(name: S, records: Records) -> Source:
        return Source(
            SourceStatus(name=name, found=True, recognised=True, last_update_ts=9), records
        )

    sources = [
        source(S.CASHFLOW, Records(events=[event(S.CASHFLOW, K.BUY, item, 1, 1, 99)])),
        source(
            S.ALLAGAN_TOOLS,
            Records(
                inventory=[Stock(item, 2), Stock(item, 3, container=12002)],
                listings=[Stock(item, 3)],
            ),
        ),
        source(S.ALLAGAN_MARKET, Records(listings=[Stock(item, 3)])),
        source(S.RESTOCKY, Records(remaining_costs=[Stock(item, 5, 4)])),
    ]
    (p,) = build_ledger(sources).positions
    assert (p.qty_open, p.listed_qty, p.avg_cost) == (5, 3, 23)
