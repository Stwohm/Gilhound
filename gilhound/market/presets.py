from collections.abc import Callable
from http.client import HTTPException
from statistics import median

from gilhound.market.http import HttpClient, parallel
from gilhound.market.profit import profit_per_unit, sellable_qty, wait_days
from gilhound.market.saddlebag import candidates
from gilhound.market.snipe import MarketRow
from gilhound.market.universalis import Universalis
from gilhound.market.volume import recent, volume_proof
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


def _opportunity(
    item: ItemRef,
    params: ScanParams,
    api: Universalis,
    now: float,
    dcs: dict[str, str],
    legacy: bool,
) -> tuple[float, MarketRow] | None:
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
        return None
    sales = (
        home.sales
        if flip
        else api.history(
            params.home_world, item.id, min_price=not snipe, entries=params.history_entries
        )
    )
    sales = recent([s for s in sales if s.hq == item.hq], now)
    cheap = [r for r in region.listings if r.world.casefold() != params.home_world.casefold()]
    if not cheap or len(sales) < params.min_history_sales:
        return None
    first = cheap[0]
    med = median(s.ppu for s in sales)
    low = min((r.ppu for r in home.listings), default=None)
    sell = min(med, low - params.undercut_gil) if snipe and low is not None else med
    proof = volume_proof(sales, now)
    velocity = proof.units_per_day
    qty = (
        first.qty
        if snipe
        else sum(r.qty for r in cheap if r.ppu == first.ppu and r.world == first.world)
    )
    rank_qty = qty
    if legacy and not snipe:
        spread = 1.1 if flip else 1.15
        rank_qty = sum(r.qty for r in cheap if r.ppu <= first.ppu * spread)
        if flip:
            velocity = home.velocity[item.hq] or velocity
    dc = dcs.get(first.world, "?")
    capped = sellable_qty(qty, velocity, params.sellable_days)
    gain = profit_per_unit(
        sell,
        first.ppu,
        seller_tax=params.seller_tax,
        buyer_tax=params.buyer_tax,
        travel_cost=params.travel_cost.get(dc, 0),
        qty=qty,
    )
    if gain <= 0 or gain < params.min_profit or first.ppu < params.min_ppu:
        return None
    buyers, days = proof.distinct_buyers_7d, proof.days_with_sales
    age = max(0, now - first.last_review_ts) / 3600
    if (
        buyers < params.min_distinct_buyers
        or days < params.min_days_with_sales
        or velocity < params.min_units_day
        or proof.top_buyer_share > params.max_top_buyer_share
        or (params.max_data_age_h is not None and age >= params.max_data_age_h)
        or (
            params.max_sale_age_h is not None
            and (proof.last_sale_age_h is None or proof.last_sale_age_h >= params.max_sale_age_h)
        )
    ):
        return None
    # A second regional price was the old alert benchmark. Live alert rules
    # compare with the home median and are applied by snipe.evaluate.
    if (
        legacy
        and snipe
        and (gain < 5000 or buyers < 3 or len(cheap) < 2 or first.ppu > 0.5 * cheap[1].ppu)
    ):
        return None
    rival = sum(r.qty for r in home.listings if r.ppu <= med)
    row = MarketRow(
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
        est_wait_days=wait_days(rival, qty, velocity),
        volume_proof=proof,
        data_age_h=age,
    )
    score = gain * capped
    if legacy:
        score = gain * min(rank_qty, velocity * params.sellable_days)
    if preset == ScanPreset.VOLUME:
        score = gain * velocity
    if snipe:
        score = gain
    return score, row


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

    def process(item: ItemRef) -> tuple[float, MarketRow] | str | None:
        try:
            return _opportunity(item, params, api, start, world_dcs, legacy_compat)
        except (OSError, HTTPException, ValueError) as exc:
            return f"Item {item.id} ({'HQ' if item.hq else 'NQ'}): {type(exc).__name__}"

    ranked = []
    for result in parallel(process, items):
        if isinstance(result, str):
            warnings.append(result)
        elif result:
            ranked.append(result)
    ranked.sort(key=lambda pair: (pair[0], pair[1].item.name, pair[1].item.hq), reverse=True)
    return ScanResult(
        params=params,
        rows=[row for _, row in ranked[: params.result_limit]],
        started_ts=int(start),
        finished_ts=int(clock()),
        warnings=warnings,
    )
