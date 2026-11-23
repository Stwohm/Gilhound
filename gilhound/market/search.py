"""Resolve item inputs and compare current whole-stack market listings."""

import json
import time
from collections.abc import Callable, Iterable, Mapping
from fractions import Fraction
from itertools import batched
from statistics import median
from urllib.error import HTTPError
from urllib.parse import urlencode

from gilhound.market.http import HttpClient
from gilhound.market.items import UNAVAILABLE, ItemCatalog, market_url
from gilhound.market.universalis import Snapshot, Universalis
from gilhound.market.volume import recent
from gilhound.market.workers import parallel
from gilhound.models import (
    DataCenter,
    ItemRef,
    Listing,
    SearchCover,
    SearchEntry,
    SearchListing,
    SearchName,
    SearchParams,
    SearchResolution,
    SearchResult,
    SearchRow,
)

XIV_SEARCH = "https://v2.xivapi.com/api/search"
REGIONS = ("Europe", "Chaos", "Light")


class SearchRegionError(ValueError):
    pass


def search_regions(centers: list[DataCenter]) -> list[str]:
    return [*REGIONS, *sorted(w.name for dc in centers for w in dc.worlds)]


def resolve_item(
    entry: SearchEntry | SearchName, *,
    marketable: set[int], names: Mapping[int, str], http: HttpClient,
) -> SearchResolution:
    def ref(iid: int, name: str | None = None) -> ItemRef:
        return ItemRef(id=iid, name=name or names.get(iid, f"Item {iid}"), hq=entry.hq_only)

    query = entry.name.strip() if isinstance(entry, SearchName) else str(entry.item_id)
    if not query:
        return SearchResolution(error="Enter an item name or ID.")
    if isinstance(entry, SearchEntry) or (query.isascii() and query.isdecimal()):
        iid = int(query)
        if iid in marketable:
            return SearchResolution(item=ref(iid))
        return SearchResolution(error=f"Item {iid} is unknown or unmarketable.")

    key = query.casefold()
    exact = [iid for iid, name in names.items() if name.casefold() == key]
    matches = [ref(iid) for iid in exact if iid in marketable]
    if exact and not matches:
        return SearchResolution(error="This item is unmarketable.")
    if not exact:
        matches = [
            ref(iid, name) for iid, name in names.items()
            if iid in marketable and name.casefold().startswith(key)
        ]
    if not matches:
        try:
            args = {
                "sheets": "Item", "fields": "Name", "limit": 100,
                "query": f'+Name~{json.dumps(query, ensure_ascii=False)} +IsUntradable=false',
            }
            found = http.request(f"{XIV_SEARCH}?{urlencode(args)}")["results"]
            matches = [
                ref(row["row_id"], row["fields"]["Name"]) for row in found
                if row["row_id"] in marketable and row["fields"].get("Name")
            ]
        except (*UNAVAILABLE, AttributeError):
            return SearchResolution(error="Item name lookup unavailable. Try an item ID.")
        matches = [item for item in matches if item.name.casefold() == key] or matches
    if len(matches) == 1:
        return SearchResolution(item=matches[0])
    if matches:
        return SearchResolution(
            candidates=sorted(matches, key=lambda item: (item.name.casefold(), item.id)),
            error="Multiple items match. Choose an item.",
        )
    return SearchResolution(error="Item is unknown or unmarketable.")


def quote(listing: Listing, dcs: Mapping[str, str], now: float) -> SearchListing:
    gross = listing.ppu * listing.qty
    seen = listing.last_review_ts
    return SearchListing(
        world=listing.world, dc=dcs.get(listing.world), ppu=listing.ppu, qty=listing.qty,
        hq=listing.hq, total=gross + gross // 20, last_review_ts=seen,
        data_age_h=max(0, now - seen) / 3600 if seen else None,
    )


def cover(
    listings: list[SearchListing], quantity: int, costs: Mapping[str, int], home_world: str,
) -> SearchCover:
    picks = []
    units = 0
    for listing in sorted(listings, key=lambda row: Fraction(row.total, row.qty)):
        picks.append(listing)
        units += listing.qty
        if units >= quantity:
            break
    worlds = {row.world: row.dc for row in picks}
    travel = sum(costs.get(dc or "", 0) for world, dc in worlds.items() if world != home_world)
    purchase = sum(row.total for row in picks)
    return SearchCover(
        requested=quantity, covered=units, listings=picks, worlds=list(worlds),
        purchase_cost=purchase, travel_cost=travel, total_cost=purchase + travel,
    )


def run_search(
    params: SearchParams, *,
    clock: Callable[[], float] = time.time,
    progress: Callable[[int, int], None] | None = None,
    home_world: str = "Cerberus",
    travel_cost: Mapping[str, int] | None = None,
    marketable: Callable[[], Iterable[int]] | None = None,
    catalog: ItemCatalog | None = None,
    http: HttpClient | None = None,
) -> SearchResult:
    started = clock()
    total = len(params.entries) * 2
    if progress:
        progress(0, total)
    http = http or HttpClient()
    api = Universalis(http)
    ids = set((marketable or (catalog.marketable if catalog else api.marketable))())
    if catalog:
        with catalog.lock:
            names = dict(catalog.names)
    else:
        names = {}
    centers = api.data_centers()
    regions = {name.casefold(): name for name in search_regions(centers)}
    region = regions.get(params.region.strip().casefold())
    if region is None:
        raise SearchRegionError("Choose Europe, Chaos, Light, or a world.")
    worlds = {w.id: w.name for dc in centers for w in dc.worlds}
    dcs = {w.name: w.dc for dc in centers for w in dc.worlds}
    home_world = next(
        (world for world in dcs if world.casefold() == home_world.casefold()), home_world
    )

    resolutions = {}
    rows = []
    for done, entry in enumerate(params.entries, 1):
        key = (type(entry), entry.item_id if isinstance(entry, SearchEntry) else entry.name,
               entry.hq_only)
        if key not in resolutions:
            resolutions[key] = resolve_item(entry, marketable=ids, names=names, http=http)
        resolved = resolutions[key]
        rows.append(SearchRow(
            entry=entry, item=resolved.item, candidates=resolved.candidates, error=resolved.error,
        ))
        if progress:
            progress(done, total)

    def current(location: str, keys: tuple[int, ...], *, home: bool) -> dict[int, Snapshot]:
        try:
            return api.current(
                location, list(keys), entries=200 if home else 0, projected=True,
                include_retainers=False, worlds=worlds,
            )
        except HTTPError as exc:
            if exc.code != 404:
                raise
            return {}

    def fetch(
        keys: tuple[int, ...],
    ) -> tuple[tuple[int, ...], dict[int, Snapshot], dict[int, Snapshot]]:
        home = current(home_world, keys, home=True)
        regional = home if region == home_world else current(region, keys, home=False)
        return keys, regional, home

    by_id: dict[int, list[SearchRow]] = {}
    for row in rows:
        if row.item:
            by_id.setdefault(row.item.id, []).append(row)
    done = total - sum(map(len, by_id.values()))
    if progress:
        progress(done, total)
    for keys, regional, home in parallel(fetch, batched(by_id, 100)):
        now = clock()
        for iid in keys:
            for row in by_id[iid]:
                populate(row, regional.get(iid), home.get(iid), dcs, now, params.top,
                         travel_cost or {}, home_world)
                done += 1
        if progress:
            progress(done, total)

    refs = [row.item for row in rows if row.item]
    for item in refs:
        item.universalis_url = market_url(item.id, region)
        item.saddlebag_url = f"https://saddlebagexchange.com/queries/item-data/{item.id}"
    if catalog:
        catalog.decorate(refs, region, wait=True)
        catalog.decorate_icons(refs)
    return SearchResult(region=region, home_world=home_world, rows=rows,
                        duration_s=max(0, clock() - started))


def populate(
    row: SearchRow, regional: Snapshot | None, home: Snapshot | None,
    dcs: Mapping[str, str], now: float, top: int, costs: Mapping[str, int], home_world: str,
) -> None:
    def quotes(snapshot: Snapshot) -> Iterable[SearchListing]:
        return (quote(r, dcs, now) for r in snapshot.listings if not row.entry.hq_only or r.hq)

    if regional is None:
        row.error = "Regional listings unavailable for this item."
    else:
        listings = list(quotes(regional))
        row.listings = listings[:top]
        if row.entry.quantity is not None:
            row.cover = cover(listings, row.entry.quantity, costs, home_world)
    if home is None:
        row.warnings.append("Home-world market data unavailable.")
    else:
        row.home_cheapest = next(quotes(home), None)
        sales = [s for s in recent(home.sales, now) if not row.entry.hq_only or s.hq]
        if sales:
            row.home_median_7d = median(s.ppu for s in sales)
            row.home_sale_age_h = max(0, now - max(s.ts for s in sales)) / 3600
