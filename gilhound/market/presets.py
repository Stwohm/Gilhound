from collections.abc import Callable
from http.client import HTTPException
from itertools import islice
from statistics import median

from gilhound.market.http import HttpClient
from gilhound.market.profit import profit_per_unit, sellable_qty, wait_days
from gilhound.market.saddlebag import candidates
from gilhound.market.snipe import MarketRow, qualifying_listings
from gilhound.market.universalis import Universalis
from gilhound.market.volume import DAY, recent, volume_proof
from gilhound.market.workers import parallel
from gilhound.models import ItemRef, ScanParams, ScanPreset, ScanResult

# The legacy scripts shipped this map and made no metadata requests.
LEGACY_WORLD_DCS = {
    world: dc
    for dc, worlds in (
        ("Chaos", "Cerberus Louisoix Moogle Omega Phantom Ragnarok Sagittarius Spriggan"),
        ("Light", "Alpha Lich Odin Phoenix Raiden Shiva Twintania Zodiark"),
    )
    for world in worlds.split()
}


def _opportunities(
    item: ItemRef,
    params: ScanParams,
    api: Universalis,
    now: float,
    dcs: dict[str, str],
    legacy: bool,
) -> list[tuple[float, MarketRow]]:
    preset = params.preset
    flip, snipe = preset == ScanPreset.FLIP, preset == ScanPreset.SNIPE
    home = api.current(
        params.home_world,
        [item.id],
        listings=params.home_listings,
        entries=params.home_entries,
        hq=item.hq,
    ).get(item.id)
    region = api.current(
        params.buy_region, [item.id], listings=params.region_listings, hq=item.hq
    ).get(item.id)
    if not home or not region:
        return []
    history = (
        home.sales
        if flip and legacy
        else api.history(
            params.home_world,
            item.id,
            min_price=not snipe,
            entries=params.history_entries,
            within=604800 if legacy else 1209600,
        )
    )
    quality_history = [s for s in history if s.hq == item.hq]
    sales = recent(quality_history, now)
    cheap = [r for r in region.listings if r.world.casefold() != params.home_world.casefold()]
    if not cheap or len(sales) < params.min_history_sales:
        return []
    med = median(s.ppu for s in sales)
    low = min((r.ppu for r in home.listings), default=None)
    sell = min(med, low - params.undercut_gil) if snipe and low is not None else med
    proof = volume_proof(sales, now)
    velocity = proof.units_per_day
    rival = sum(r.qty for r in home.listings if r.ppu <= med)
    daily = [0] * 14
    previous = []
    for sale in quality_history:
        day = int((now - sale.ts) // DAY)
        if 0 <= day < 14:
            daily[13 - day] += sale.qty
            if day >= 7:
                previous.append(sale.ppu)
    mixed = recent(history, now)
    if legacy and flip:
        velocity = home.velocity[item.hq] or velocity
    buyers = proof.distinct_buyers_7d
    if (
        buyers < params.min_distinct_buyers
        or proof.days_with_sales < params.min_days_with_sales
        or velocity < params.min_units_day
        or proof.top_buyer_share > params.max_top_buyer_share
        or (
            params.max_sale_age_h is not None
            and (proof.last_sale_age_h is None or proof.last_sale_age_h >= params.max_sale_age_h)
        )
    ):
        return []
    units = sum(s.qty for s in sales)
    min_sale = min(s.ppu for s in sales)
    max_sale = max(s.ppu for s in sales)
    hq_share = sum(s.hq for s in mixed) / len(mixed)
    sellers = len({r.retainer_hash for r in home.listings} - {""})
    below_sell = sum(r.qty for r in home.listings if r.ppu < sell)
    trend = 100 * (med / median(previous) - 1) if previous else None
    results = []
    for index, first in enumerate(cheap if snipe and not legacy else cheap[:1]):
        next_price = cheap[index + 1].ppu if index + 1 < len(cheap) else None
        qty = (
            first.qty
            if snipe
            else sum(r.qty for r in cheap if r.ppu == first.ppu and r.world == first.world)
        )
        rank_qty = qty
        if legacy and not snipe:
            spread = 1.1 if flip else 1.15
            rank_qty = sum(r.qty for r in cheap if r.ppu <= first.ppu * spread)
        dc = dcs.get(first.world, "?")
        capped = sellable_qty(qty, velocity, params.sellable_days)
        if not legacy and capped == 0:
            continue
        gain = profit_per_unit(
            sell,
            first.ppu,
            seller_tax=params.seller_tax,
            buyer_tax=params.buyer_tax,
            travel_cost=params.travel_cost.get(dc, 0),
            qty=qty if legacy else capped,
        )
        if gain <= 0 or gain < params.min_profit or first.ppu < params.min_ppu:
            continue
        age = max(0, now - first.last_review_ts) / 3600
        if params.max_data_age_h is not None and age >= params.max_data_age_h:
            continue
        # A second regional price was the old alert benchmark. Live alert rules
        # compare with the home median and are applied by snipe.evaluate.
        if (
            legacy
            and snipe
            and (gain < 5000 or buyers < 3 or len(cheap) < 2 or first.ppu > 0.5 * cheap[1].ppu)
        ):
            continue
        nearby = [r for r in region.listings if r.ppu * 100 <= first.ppu * 110]
        unit_cost = first.ppu * (1 + params.buyer_tax)
        row = MarketRow(
            units_sold_7d=units,
            sales_7d=len(sales),
            average_sale_size=units / len(sales),
            min_sale_price=min_sale,
            max_sale_price=max_sale,
            hq_sale_share=hq_share,
            home_listing_count=len(home.listings),
            distinct_sellers=sellers,
            rival_stock=rival,
            units_below_sell=below_sell,
            nearby_listings=len(nearby),
            nearby_units=sum(r.qty for r in nearby),
            second_buy_price=next_price,
            next_listing_gap=next_price - first.ppu if next_price is not None else None,
            roi_pct=gain / unit_cost * 100,
            capital_needed=capped * unit_cost,
            profit_per_day=gain * min(proof.units_per_day, capped),
            days_to_sell=capped / proof.units_per_day,
            daily_units_14d=daily,
            trend_pct=trend,
            listing=first,
            item=item,
            buy_world=first.world,
            buy_dc=dc,
            buy_ppu=first.ppu,
            buy_qty_at_price=qty,
            home_median_7d=med,
            home_lowest=low,
            sellable_qty=capped,
            profit_per_unit=int(gain) if legacy else gain,
            total_profit_capped=gain * capped,
            est_wait_days=wait_days(rival, qty if legacy else capped, velocity),
            volume_proof=proof,
            data_age_h=age,
        )
        score = gain * capped
        if legacy:
            score = gain * min(rank_qty, velocity * params.sellable_days)
        if preset == ScanPreset.VOLUME:
            score = gain * velocity if legacy else row.profit_per_day
        if snipe:
            score = gain
        results.append((score, row))
    return results


def run_scan(
    params: ScanParams,
    *,
    clock: Callable[[], float],
    legacy_compat: bool = False,
    http: HttpClient | None = None,
    world_dcs: dict[str, str] | None = None,
) -> ScanResult:
    """Scan with caller-supplied time. For historical scores also set buyer_tax=0."""
    start = clock()
    client = http or HttpClient()
    api = Universalis(client)
    if legacy_compat and world_dcs is None:
        world_dcs = LEGACY_WORLD_DCS
    if world_dcs is None:
        world_dcs = {w.name: dc.name for dc in api.data_centers() for w in dc.worlds}
    items = candidates(params, client)
    warnings = []

    def process(item: ItemRef) -> list[tuple[float, MarketRow]] | str:
        try:
            return _opportunities(item, params, api, start, world_dcs, legacy_compat)
        except (OSError, HTTPException, ValueError) as exc:
            return f"Item {item.id} ({'HQ' if item.hq else 'NQ'}): {type(exc).__name__}"

    ranked = []
    for result in parallel(process, items):
        if isinstance(result, str):
            warnings.append(result)
        elif result:
            ranked.extend(result)
    ranked.sort(key=lambda pair: (pair[0], pair[1].item.name, pair[1].item.hq), reverse=True)
    finished = int(clock())
    rows = (row for _, row in ranked)
    if not legacy_compat and params.preset == ScanPreset.SNIPE:
        if params.snipe_rule is not None and params.freshness_tiers is not None:
            qualified = qualifying_listings(
                rows, params.snipe_rule, params.freshness_tiers, finished
            )
            selected = [row for row, *_ in islice(qualified, params.result_limit)]
        else:
            # Low-level callers can supply their rules later to evaluate().
            selected = list(rows)
    else:
        selected = list(islice(rows, params.result_limit))
    return ScanResult(
        params=params,
        rows=selected,
        started_ts=int(start),
        finished_ts=finished,
        warnings=warnings,
    )
