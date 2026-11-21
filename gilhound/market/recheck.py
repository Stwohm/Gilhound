"""Refresh only the buy world and home evidence for one item quality."""

import time
from collections.abc import Callable
from statistics import median

from gilhound.market.http import HttpClient
from gilhound.market.universalis import Universalis
from gilhound.market.volume import recent
from gilhound.models import Listing, QuoteRequest, QuoteSnapshot


def recheck(
    query: QuoteRequest, *, api: Universalis | None = None, clock: Callable[[], float] = time.time
) -> QuoteSnapshot:
    api = api or Universalis(HttpClient())

    def listings(world: str) -> list[Listing]:
        snapshot = api.current(world, [query.item_id], listings=100, hq=query.hq).get(query.item_id)
        if snapshot is None:
            raise ValueError("World snapshot unavailable")
        return [r for r in snapshot.listings if r.hq == query.hq and
                r.world.casefold() == world.casefold()]

    buy = listings(query.buy_world)
    home = buy if query.home_world == query.buy_world else listings(query.home_world)
    history = api.history(query.home_world, query.item_id, entries=query.history_entries)
    now = clock()
    prices = [s.ppu for s in recent(history, now) if s.hq == query.hq]
    price = buy[0].ppu if buy else None
    matches = [r for r in buy if r.ppu == price]
    oldest = min((r.last_review_ts for r in matches), default=0)
    return QuoteSnapshot(price=price, qty=sum(r.qty for r in matches),
                         stacks=[r.qty for r in matches],
                         age_h=max(0, now - oldest) / 3600 if oldest else None,
                         home_lowest=home[0].ppu if home else None,
                         home_median=median(prices) if prices else None)
