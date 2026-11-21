"""An independent unit-level replay of the raw anonymised database."""

import sqlite3
from collections import defaultdict, deque
from contextlib import closing
from datetime import timedelta, timezone
from pathlib import Path

from gilhound.ingest import read_plugins
from gilhound.ledger import build_ledger
from gilhound.models import LedgerEventKind, SourceName

ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "plugins"


def test_raw_cashflow_per_item_and_variant() -> None:
    sources = read_plugins(ROOT, local_tz=timezone(timedelta(hours=3)))
    ledger = build_ledger(sources[:1])
    costs: dict[int, deque[int]] = defaultdict(deque)
    realised: dict[int, int] = defaultdict(int)
    with closing(
        sqlite3.connect((ROOT / "CashFlow" / "DefaultDbV2.db").as_uri() + "?mode=ro", uri=True)
    ) as db:
        proceeds = dict(db.execute("SELECT Item, SUM(Price) FROM RetainerSales GROUP BY Item"))
        rows = db.execute("""
            SELECT UnixTime, Item, Quantity, Price * Quantity +
                CASE WHEN IsMannequinn = 0 THEN Price * Quantity / 20 ELSE 0 END, 0 AS sold
                FROM ShopPurchases
            UNION ALL SELECT UnixTime, Item, Quantity, Price, 1 FROM RetainerSales
            ORDER BY UnixTime, sold
        """)
        for _, item, qty, total, sold in rows:
            if sold:
                realised[item] += sum(
                    costs[item].popleft() for _ in range(min(qty, len(costs[item])))
                )
            else:
                costs[item].extend(total * (i + 1) // qty - total * i // qty for i in range(qty))
    for (base, hq), pnl in ledger.per_item.items():
        raw = base + 1_000_000 * hq
        assert pnl.sales_proceeds == proceeds.get(raw, 0)
        assert pnl.known_cost_of_goods_sold == realised[raw]
    assert ledger.summary.sales_proceeds == sum(proceeds.values())
    assert ledger.summary.known_cost_of_goods_sold == sum(realised.values())
    merged = build_ledger(sources)
    observations = [
        e for source in sources for e in source.records.events if e.kind == LedgerEventKind.SELL
    ]
    kept = [e for e in merged.events if e.kind == LedgerEventKind.SELL]
    assert len(kept) < len(observations)
    assert [e for e in kept if e.source == SourceName.CASHFLOW] == [
        e for e in ledger.events if e.kind == LedgerEventKind.SELL
    ]
