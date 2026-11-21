"""FIFO uses differences of cumulative integer allocations to conserve stack gil."""

from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass, field

from gilhound.ingest.common import Transaction
from gilhound.models import ItemRef, LedgerEvent, LedgerEventKind, PnLSummary, Position, SourceName

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
    warnings: list[str] = field(default_factory=list)


def stack_total(row: LedgerEvent) -> int | None:
    if isinstance(row, Transaction) and row.stack_gil is not None:
        return row.stack_gil
    return row.ppu * row.qty if row.ppu is not None else None


def merge_events(events: Iterable[LedgerEvent]) -> list[LedgerEvent]:
    """Match one sale per source per item/HQ/qty within 300s of the preferred sale.

    Prefer CashFlow, then Restocky's net proceeds, then AllaganMarket's estimate.
    Keep same-source repeats. For ambiguous candidates use the nearest timestamp.
    """
    priority = {SourceName.CASHFLOW: 0, SourceName.RESTOCKY: 1, SourceName.ALLAGAN_MARKET: 2}
    type Candidate = tuple[int, int, set[SourceName]]
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
                (ts, sources)
                for interval in range(bucket - 1, bucket + 2)
                for ts, proceeds, sources in groups.get((*key, interval), ())
                if row.source not in sources and abs(row.ts - ts) <= 300
                and abs(total - proceeds) <= tolerance
            )
            match = min(nearby, key=lambda pair: abs(row.ts - pair[0]), default=None)
            if match is not None:
                match[1].add(row.source)
                continue
            groups[*key, bucket].append((row.ts, total, {row.source}))
        kept.append((row.ts, index, row))
    return [row for _, _, row in sorted(kept)]


@dataclass
class Lot:
    qty: int
    total: int | None
    used: int = 0
    purchased: bool = False

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
    pnl: PnLSummary = field(default_factory=empty_summary)

    def acquire(
        self, qty: int, total: int | None, override: int | None, purchased: bool = False
    ) -> None:
        if total is None and override is not None:
            total = qty * override
        self.lots.append(Lot(qty, total, purchased=purchased))
        self.qty += qty

    def apply(self, row: LedgerEvent, adjustment: ItemAdjustment | None) -> None:
        override = adjustment.unknown_unit_cost if adjustment else None
        total = stack_total(row)
        if row.kind in (LedgerEventKind.BUY, LedgerEventKind.OPENING, LedgerEventKind.TRADE_IN):
            self.acquire(row.qty, total, override, row.kind == LedgerEventKind.BUY)
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
            while proceeds.used < row.qty:
                lot = self.lots[0]
                qty = min(row.qty - proceeds.used, lot.qty - lot.used)
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
    entries = merge_events(
        [*(LedgerEvent(**a.model_dump(), source=None) for a in manual.acquisitions), *events]
    )
    holdings: dict[ItemKey, Holding] = {}
    for row in entries:
        key = row.item.id, row.item.hq
        if key not in holdings:
            holdings[key] = Holding(row.item)
        holdings[key].apply(row, overrides.get(key))
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
    return Ledger(entries, positions, summary, per_item)
