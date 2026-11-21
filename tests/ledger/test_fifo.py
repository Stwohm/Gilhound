from pathlib import Path

from gilhound.ingest.common import Records, Source, Stock, event, item_ref
from gilhound.ledger import Acquisition, ItemAdjustment, ManualAdjustments, build_ledger, calculate
from gilhound.models import LedgerEventKind as K
from gilhound.models import SourceName as S
from gilhound.models import SourceStatus


def test_partial_split_quality_unlist() -> None:
    nq, hq = item_ref(1200, False), item_ref(1200, True)
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
    assert final.per_item[1200, False].realised_profit == 84


def test_unknown_manual_and_personal(tmp_path: Path) -> None:
    item = item_ref(1000, True)
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
    assert personal.positions == []


def test_conflicting_sale_proceeds_consume_both_lots() -> None:
    item = item_ref(1000, True)
    ledger = calculate([
        event(S.CASHFLOW, K.BUY, item, 4, 1, 48),
        event(S.CASHFLOW, K.SELL, item, 2, 10, 100),
        event(S.RESTOCKY, K.SELL, item, 2, 11, 1000),
    ])
    assert ledger.summary.sales_proceeds == 1100
    assert ledger.summary.known_cost_of_goods_sold == 48
    assert ledger.positions[0].qty_open == 0


def test_one_to_one_cross_source_sale() -> None:
    item = item_ref(1000, True)
    rows = [
        event(s, K.SELL, item, 2, ts, total)
        for s, ts, total in [
            (S.ALLAGAN_MARKET, 400, 205),
            (S.ALLAGAN_MARKET, 400, 203),
            (S.CASHFLOW, 100, 202),
            (S.CASHFLOW, 100, 201),
            (S.RESTOCKY, 100, 202),
            (S.ALLAGAN_MARKET, 401, 200),
        ]
    ]
    result = calculate(rows)
    assert len(result.events) == 4
    assert result.summary.sales_proceeds == 808


def test_exact_net_stacks_with_same_unit_price_are_distinct() -> None:
    item = item_ref(1000, True)
    ledger = calculate([
        event(S.CASHFLOW, K.SELL, item, 3, 10, 100),
        event(S.RESTOCKY, K.SELL, item, 3, 11, 101),
    ])
    assert ledger.summary.sales_proceeds == 201


def test_snapshot_overlap_and_remaining_cost() -> None:
    item = item_ref(800, False)

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
        source(S.ALLAGAN_MARKET, Records(listings=[Stock(item, 1), Stock(item, 2)])),
        source(S.RESTOCKY, Records(remaining_costs=[Stock(item, 5, 4)])),
    ]
    (p,) = build_ledger(sources).positions
    assert p.listing_count == 2
    assert (p.qty_open, p.listed_qty, p.avg_cost) == (3, 3, 107 / 3)


def test_positions_require_flip_evidence() -> None:
    bag, listed, bought, sold = (item_ref(i, False) for i in range(1000, 1004))
    excluded = [item_ref(i, False) for i in (1, 2, 8, 19, 28, 21072)]
    records = Records(
        inventory=[
            Stock(bag, 90, container=0),
            Stock(listed, 99, container=1000),
            Stock(bought, 2, container=10006),
        ],
        listings=[Stock(listed, 3), *(Stock(i, 50) for i in excluded)],
        events=[
            event(S.CASHFLOW, K.BUY, bought, 2, 1, 30),
            event(S.CASHFLOW, K.BUY, sold, 1, 1, 9),
            event(S.CASHFLOW, K.SELL, sold, 1, 2, 12),
        ],
    )
    src = Source(SourceStatus(name=S.ALLAGAN_TOOLS, found=True, recognised=True), records)
    positions = build_ledger([src]).positions
    opened = {p.item.id: p for p in positions if p.qty_open}
    assert set(opened) == {listed.id, bought.id}
    assert opened[listed.id].listed_qty == opened[listed.id].qty_open == 3
    assert opened[listed.id].avg_cost is None
    assert opened[bought.id].listed_qty == 0
    assert opened[bought.id].known_capital == 30
    assert [(p.item.id, p.realised_profit) for p in positions if not p.qty_open] == [(sold.id, 3)]


def test_sorted_storage_caps_only_open_purchases(tmp_path: Path) -> None:
    from gilhound.ingest import allagan_tools

    containers = [
        0,
        1,
        2,
        3,
        4000,
        4001,
        4100,
        4101,
        *range(10000, 10007),
        1000,
        11000,
        3202,
        3300,
        2500,
        2501,
        12002,
    ]
    rows = []
    events = []
    for i, container in enumerate(containers):
        item = item_ref(1000 + i, False)
        row = ["0"] * 25 + ["", ""]
        # Virtual wardrobe slots are exported with raw Container 0.
        row[0] = str(container if container not in (2500, 2501) else 0)
        row[2], row[3], row[20] = str(item.id), "2", str(container)
        rows.append(",".join(row))
        events.append(event(S.CASHFLOW, K.BUY, item, 5, 1, 50))
    absent = item_ref(2000, False)
    events.append(event(S.CASHFLOW, K.BUY, absent, 1, 1, 10))
    (tmp_path / "inventories.csv").write_text("\n".join(rows))
    cash = Source(
        SourceStatus(name=S.CASHFLOW, found=True, recognised=True), Records(events=events)
    )
    ledger = build_ledger([cash, allagan_tools.read(tmp_path)])
    assert {p.item.id for p in ledger.positions} == {
        1000 + i for i in [*range(15), len(containers) - 1]
    }
    assert all(p.qty_open == 2 and p.known_capital == 20 for p in ledger.positions)
    assert not ledger.warnings
    fallback = build_ledger([cash, allagan_tools.read(tmp_path / "missing")])
    assert len(fallback.positions) == len(events)
    assert fallback.warnings
