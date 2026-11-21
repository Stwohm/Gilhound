"""Entry points for transaction replay and reconciliation against current snapshots."""

from collections import defaultdict
from collections.abc import Iterable

from gilhound.ingest.allagan_tools import SELLABLE
from gilhound.ingest.common import Source, Stock, event
from gilhound.models import ItemRef, LedgerEventKind, SourceName

from .adjustments import Acquisition, ItemAdjustment, ManualAdjustments
from .fifo import ItemKey, Ledger, calculate, merge_events

__all__ = [
    "Acquisition",
    "ItemAdjustment",
    "Ledger",
    "ManualAdjustments",
    "build_ledger",
    "calculate",
    "merge_events",
]


def build_ledger(sources: Iterable[Source], adjustments: ManualAdjustments | None = None) -> Ledger:
    """Replay history then reconcile current stock as a lower bound on holdings.

    Use one complete listing snapshot (newest file mtime, AllaganMarket first
    on ties). Inventory excludes the market container before adding listings.
    Remaining Restocky purchases overlap historical lots; only uncovered units
    create new lots. Snapshots cannot supply a historical cost for past sales.
    """
    manual = adjustments or ManualAdjustments()
    inputs = [s for s in sources if s.status.recognised]
    history = [e for s in inputs for e in s.records.events]
    replay = calculate(history, manual)
    existing = {(p.item.id, p.item.hq): p.qty_open for p in replay.positions}
    ts = max([0, *(e.ts for e in replay.events), *(s.status.last_update_ts or 0 for s in inputs)])
    listings = [s for s in inputs if s.records.listings is not None]
    rank = {SourceName.ALLAGAN_MARKET: 2, SourceName.ALLAGAN_TOOLS: 1}
    latest = max(
        listings,
        key=lambda s: (s.records.listings_mtime or 0, rank.get(s.status.name, 0)),
        default=None,
    )
    manual_costs = {
        (a.item.id, a.item.hq)
        for a in manual.items
        if a.unknown_unit_cost is not None and not a.personal
    }
    inventory: dict[ItemKey, int] = defaultdict(int)
    listed: dict[ItemKey, int] = defaultdict(int)
    costs: dict[ItemKey, list[Stock]] = defaultdict(list)
    items: dict[ItemKey, ItemRef] = {}
    sellable: dict[ItemKey, int] = defaultdict(int)
    for source in inputs:
        for stock in source.records.inventory or []:
            key = stock.item.id, stock.item.hq
            if stock.container not in SELLABLE:
                continue
            sellable[key] += stock.qty
            if key in manual_costs:
                inventory[key] += stock.qty
                items[key] = stock.item
        for stock in source.records.remaining_costs:
            key = stock.item.id, stock.item.hq
            costs[key].append(stock)
            items[key] = stock.item
    if latest:
        for stock in latest.records.listings:
            key = stock.item.id, stock.item.hq
            listed[key] += stock.qty
            items[key] = stock.item
    for key, item in items.items():
        held = existing.get(key, 0)
        # Prefer the newest remaining lots when older units already have a basis.
        target = listed[key] + inventory[key]
        overlap = held
        for stock in sorted(costs[key], key=lambda s: s.ts):
            covered = min(overlap, stock.qty)
            overlap -= covered
            more = min(stock.qty - covered, max(0, target - held))
            if more:
                history.append(
                    event(
                        SourceName.RESTOCKY,
                        LedgerEventKind.OPENING,
                        item,
                        more,
                        ts,
                        more * stock.ppu,
                    )
                )
                held += more
        missing = target - held
        if missing > 0:
            source_name = SourceName.ALLAGAN_TOOLS if inventory[key] else latest.status.name
            history.append(event(source_name, LedgerEventKind.OPENING, item, missing, ts, None))
        if listed[key]:
            history.append(
                event(latest.status.name, LedgerEventKind.LIST, item, listed[key], ts, None)
            )
    limits = None
    if any(
        s.status.name == SourceName.ALLAGAN_TOOLS and s.records.inventory is not None
        for s in inputs
    ):
        limits = {key: sellable[key] + listed[key] for key in set(sellable) | set(listed)}
    ledger = calculate(history, manual, limits)
    if limits is None:
        ledger.warnings.append(
            "Allagan Tools inventory unavailable. Using purchase history and listings."
        )
    return ledger
