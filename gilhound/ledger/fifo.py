"""FIFO uses differences of cumulative integer allocations to conserve stack gil."""

from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass, field

from gilhound.ingest.common import Transaction
from gilhound.models import (
    ItemRef,
    LedgerEvent,
    LedgerEventKind,
    PnLSummary,
    Position,
    RealisedSale,
    SalesByItem,
    SourceName,
)

from .adjustments import ItemAdjustment, ManualAdjustments

# XIVAPI ItemUICategory "Currency". IDs below 100 are reserved for
# gil, elemental crystals and low-ID currencies (including tomestones).
CURRENCIES = {
    10307,
    10308,
    10309,
    10310,
    10311,
    17833,
    17834,
    21072,
    21073,
    21074,
    21075,
    21076,
    21077,
    21078,
    21079,
    21080,
    21081,
    21172,
    21935,
    22525,
    25199,
    25200,
    26533,
    26807,
    28063,
    28186,
    28187,
    28188,
    30341,
    33913,
    33914,
    36656,
    36657,
    37854,
    38534,
    38952,
    39885,
    41079,
    41629,
    41784,
    41785,
    44472,
    46178,
    48084,
    49125,
    52322,
}


type ItemKey = tuple[int, bool]


@dataclass
class Ledger:
    events: list[LedgerEvent]
    positions: list[Position]
    summary: PnLSummary
    per_item: dict[ItemKey, PnLSummary]
    sales: list[RealisedSale] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


    def filtered_sales(self, since: int | None = None, until: int | None = None,
                       item: int | None = None, retainer: str | None = None) -> list[RealisedSale]:
        return [s for s in self.sales if (since is None or s.ts >= since)
                and (until is None or s.ts < until) and (item is None or s.item.id == item)
                and (retainer is None or s.retainer == retainer)]


def rollup_sales(rows: Iterable[RealisedSale]) -> list[SalesByItem]:
    groups: dict[ItemKey, SalesByItem] = {}
    timed_gil: dict[ItemKey, int] = defaultdict(int)
    slot_days: dict[ItemKey, float] = defaultdict(float)
    for row in rows:
        key = row.item.id, row.item.hq
        if key not in groups:
            groups[key] = SalesByItem(item=row.item)
        group = groups[key]
        group.sales += 1
        group.qty += row.qty
        group.net += row.net
        group.known_profit += row.known_profit
        group.unknown_qty += row.unknown_qty
        if row.profit is not None and row.listing_days and row.listing_days > 0:
            timed_gil[key] += row.profit
            slot_days[key] += row.listing_days
    for key, days in slot_days.items():
        groups[key].profit_per_slot_day = timed_gil[key] / days
    return sorted(groups.values(), key=lambda g: (-g.known_profit, g.item.name, g.item.hq))


def stack_total(row: LedgerEvent) -> int | None:
    if isinstance(row, Transaction) and row.stack_gil is not None:
        return row.stack_gil
    return row.ppu * row.qty if row.ppu is not None else None


def merge_events(
    events: Iterable[LedgerEvent], enrichment: dict[int, Transaction] | None = None
) -> list[LedgerEvent]:
    """Match one sale per source per item/HQ/qty within 300s of the preferred sale.

    Prefer CashFlow, then Restocky's net proceeds, then AllaganMarket's estimate.
    Keep same-source repeats. For ambiguous candidates use the nearest timestamp.
    """
    priority = {SourceName.CASHFLOW: 0, SourceName.RESTOCKY: 1, SourceName.ALLAGAN_MARKET: 2}
    type Candidate = tuple[int, int, set[SourceName], LedgerEvent]
    groups: dict[tuple[int, bool, int, int], list[Candidate]] = defaultdict(list)
    kept = []
    for index, row in sorted(
        enumerate(events), key=lambda pair: (priority.get(pair[1].source, 3), pair[1].ts)
    ):
        if row.kind == LedgerEventKind.SELL and row.source is not None:
            key = row.item.id, row.item.hq, row.qty
            bucket = row.ts // 300
            total = stack_total(row)
            if total is None:
                raise ValueError("Sale proceeds required")
            # Exact net stacks must agree. AllaganMarket estimates tax from gross,
            # so its net can differ by one gil at the rounding boundary.
            tolerance = 1 if row.source == SourceName.ALLAGAN_MARKET else 0
            nearby = (
                (ts, sources, original)
                for interval in range(bucket - 1, bucket + 2)
                for ts, proceeds, sources, original in groups.get((*key, interval), ())
                if row.source not in sources and abs(row.ts - ts) <= 300
                and abs(total - proceeds) <= tolerance
            )
            match = min(nearby, key=lambda pair: abs(row.ts - pair[0]), default=None)
            if match is not None:
                match[1].add(row.source)
                original = match[2]
                if enrichment is not None and id(original) in enrichment and isinstance(
                    row, Transaction
                ):
                    original = enrichment[id(original)]
                    original.sources.append(row.source)
                    for name, value in row.details.model_dump(exclude_defaults=True).items():
                        if getattr(original.details, name) in (None, {}):
                            setattr(original.details, name, value)
                    if original.item.name == f"Item {original.item.id}":
                        original.item = row.item
                continue
            if enrichment is not None and isinstance(row, Transaction):
                enriched = row.model_copy(deep=True)
                enriched.sources = [row.source]
                enrichment[id(row)] = enriched
            groups[*key, bucket].append((row.ts, total, {row.source}, row))
        kept.append((row.ts, index, row))
    return [row for _, _, row in sorted(kept)]


@dataclass
class Lot:
    qty: int
    total: int | None
    used: int = 0
    purchased: bool = False
    acquired_ts: int | None = None

    def take(self, qty: int) -> int | None:
        before = self.used
        self.used += qty
        if self.total is None:
            return None
        return self.total * self.used // self.qty - self.total * before // self.qty


def empty_summary() -> PnLSummary:
    return PnLSummary(
        realised_profit=0, sales_proceeds=0, known_cost_of_goods_sold=0, unknown_cost_qty_sold=0
    )


@dataclass
class Holding:
    item: ItemRef
    lots: deque[Lot] = field(default_factory=deque)
    qty: int = 0
    listed: int = 0
    sales: list[RealisedSale] = field(default_factory=list)
    pnl: PnLSummary = field(default_factory=empty_summary)

    def acquire(
        self, qty: int, total: int | None, override: int | None, purchased: bool = False,
        acquired_ts: int | None = None,
    ) -> None:
        if total is None and override is not None:
            total = qty * override
        self.lots.append(Lot(qty, total, purchased=purchased, acquired_ts=acquired_ts))
        self.qty += qty

    def apply(self, row: LedgerEvent, adjustment: ItemAdjustment | None) -> None:
        override = adjustment.unknown_unit_cost if adjustment else None
        total = stack_total(row)
        if row.kind in (LedgerEventKind.BUY, LedgerEventKind.OPENING, LedgerEventKind.TRADE_IN):
            self.acquire(row.qty, total, override, row.kind == LedgerEventKind.BUY,
                         row.ts if row.kind == LedgerEventKind.BUY else None)
        elif row.kind == LedgerEventKind.LIST:
            self.listed += row.qty
            if self.listed > self.qty:
                self.acquire(self.listed - self.qty, None, override)
        elif row.kind == LedgerEventKind.UNLIST:
            self.listed = max(0, self.listed - row.qty)
        else:
            if total is None:
                raise ValueError("Sale proceeds required")
            if row.qty > self.qty:
                self.acquire(row.qty - self.qty, None, override)
            proceeds = Lot(row.qty, total)
            personal = adjustment and adjustment.personal
            if not personal:
                self.pnl.sales_proceeds += total
            before = self.pnl.model_copy()
            holding_seconds = 0
            dated_qty = 0
            while proceeds.used < row.qty:
                lot = self.lots[0]
                qty = min(row.qty - proceeds.used, lot.qty - lot.used)
                if lot.acquired_ts is not None:
                    holding_seconds += (row.ts - lot.acquired_ts) * qty
                    dated_qty += qty
                cost = lot.take(qty)
                received = proceeds.take(qty)
                if not personal:
                    if cost is None:
                        self.pnl.unknown_cost_qty_sold += qty
                    else:
                        self.pnl.known_cost_of_goods_sold += cost
                        self.pnl.realised_profit += received - cost
                if lot.used == lot.qty:
                    self.lots.popleft()
            if not personal:
                unknown = self.pnl.unknown_cost_qty_sold - before.unknown_cost_qty_sold
                basis = self.pnl.known_cost_of_goods_sold - before.known_cost_of_goods_sold
                profit = self.pnl.realised_profit - before.realised_profit
                details = row.details.model_dump() if isinstance(row, Transaction) else {}
                first = details.get("first_listed")
                self.sales.append(RealisedSale(
                    item=row.item, ts=row.ts, qty=row.qty, net=total, fifo_cost=None if unknown
                    else basis, unknown_qty=unknown, known_profit=profit,
                    roi_pct=100 * profit / basis if basis and not unknown else None,
                    purchase_days=holding_seconds / (86400 * row.qty) if dated_qty == row.qty
                    else None, listing_days=(row.ts - first) / 86400 if first is not None
                    and first <= row.ts else None,
                    sources=row.sources if isinstance(row, Transaction) else
                    ([row.source] if row.source else []), **details,
                ))
            self.qty -= row.qty
            self.listed = max(0, self.listed - row.qty)

    def reconcile(self, available: int) -> None:
        excess = max(0, self.qty - available)
        for lot in self.lots:
            if lot.purchased and excess:
                removed = min(excess, lot.qty - lot.used)
                lot.take(removed)
                self.qty -= removed
                excess -= removed

    def position(self) -> Position:
        known = all(lot.total is not None for lot in self.lots)
        remaining = sum(
            lot.total - lot.total * lot.used // lot.qty
            for lot in self.lots
            if lot.total is not None
        )
        return Position(
            item=self.item,
            qty_open=self.qty,
            known_capital=remaining,
            listed_qty=self.listed,
            cost_known=known,
            avg_cost=remaining / self.qty if known and self.qty else None,
            realised_profit=self.pnl.realised_profit,
        )


def calculate(
    events: Iterable[LedgerEvent],
    adjustments: ManualAdjustments | None = None,
    available: dict[ItemKey, int] | None = None,
) -> Ledger:
    manual = adjustments or ManualAdjustments()
    overrides = {(a.item.id, a.item.hq): a for a in manual.items}
    enrichment: dict[int, Transaction] = {}
    entries = merge_events(
        [*(LedgerEvent(**a.model_dump(), source=None) for a in manual.acquisitions), *events],
        enrichment,
    )
    holdings: dict[ItemKey, Holding] = {}
    for row in entries:
        key = row.item.id, row.item.hq
        if key not in holdings:
            holdings[key] = Holding(row.item)
        holdings[key].apply(enrichment.get(id(row), row), overrides.get(key))
    per_item = {key: h.pnl for key, h in holdings.items()}
    summary = PnLSummary(
        **{k: sum(getattr(p, k) for p in per_item.values()) for k in PnLSummary.model_fields}
    )
    absent = set()
    if available is not None:
        for key, holding in holdings.items():
            if holding.qty:
                holding.reconcile(available.get(key, 0))
                if not holding.qty:
                    absent.add(key)
    positions = [
        h.position()
        for key, h in holdings.items()
        if key not in absent
        and h.item.id >= 100
        and h.item.id not in CURRENCIES
        and not (key in overrides and overrides[key].personal)
    ]
    sales = sorted((s for h in holdings.values() for s in h.sales), key=lambda s: -s.ts)
    return Ledger(entries, positions, summary, per_item, sales=sales)
