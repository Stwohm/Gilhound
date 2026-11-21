from collections import Counter
from collections.abc import Sequence

from gilhound.models import Sale, VolumeProof

DAY = 86400


def recent(sales: Sequence[Sale], now: float) -> list[Sale]:
    return [sale for sale in sales if 0 <= now - sale.ts < 7 * DAY]


def volume_proof(sales: Sequence[Sale], now: float) -> VolumeProof:
    week = recent(sales, now)
    buyers: Counter[str] = Counter()
    for sale in week:
        buyers[sale.buyer_hash] += sale.qty
    units = buyers.total()
    latest = max((sale.ts for sale in sales if sale.ts <= now), default=None)
    return VolumeProof(
        units_per_day=units / 7,
        distinct_buyers_7d=len(buyers.keys() - {""}),
        last_sale_age_h=None if latest is None else (now - latest) / 3600,
        top_buyer_share=max(buyers.values()) / units if units else 0,
        days_with_sales=len({int((now - s.ts) // DAY) for s in week}),
    )
