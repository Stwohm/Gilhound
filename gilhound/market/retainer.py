from collections.abc import Callable, Iterable, Mapping
from itertools import batched

from gilhound.market.http import HttpClient
from gilhound.market.profit import profit_per_unit
from gilhound.market.universalis import Snapshot, Universalis
from gilhound.market.workers import parallel
from gilhound.models import (
    ItemRef,
    Listing,
    RetainerItemRow,
    RetainerScanParams,
    RetainerScanResult,
)


def batches(
    api: Universalis, location: str, ids: Iterable[int], worlds: dict[int, str]
) -> Iterable[tuple[list[int], dict[int, Snapshot]]]:
    def fetch(group: tuple[int, ...]) -> tuple[list[int], dict[int, Snapshot]]:
        keys = list(group)
        return keys, api.current(location, keys, projected=True, worlds=worlds)

    return parallel(fetch, batched(ids, 100))


def run_retainer_scan(
    params: RetainerScanParams,
    *,
    clock: Callable[[], float],
    progress: Callable[[int, int], None] | None = None,
    home_world: str = "Cerberus",
    buy_region: str = "Europe",
    travel_cost: Mapping[str, int] | None = None,
    marketable: Callable[[], Iterable[int]] | None = None,
    item_names: Mapping[int, str] | None = None,
    http: HttpClient | None = None,
) -> RetainerScanResult:
    costs = travel_cost or {}
    start = clock()
    api = Universalis(http or HttpClient())
    world, region = params.world or home_world, params.buy_region or buy_region
    centers = api.data_centers()
    names = {w.id: w.name for dc in centers for w in dc.worlds}
    dcs = {w.name: w.dc for dc in centers for w in dc.worlds}
    ids = sorted(set((marketable or api.marketable)()))
    matched: dict[int, list[Listing]] = {}
    done = checked = 0
    warnings = []
    if progress:
        progress(0, len(ids))
    for group, snapshots in batches(api, world, ids, names):
        for iid, snapshot in snapshots.items():
            rows = [
                r
                for r in snapshot.listings
                if (r.retainer_name or "").casefold() == params.retainer_name.casefold()
            ]
            if rows:
                matched[iid] = rows
        done += len(group)
        checked += len(snapshots)
        if progress:
            progress(done, len(ids))
    if checked < len(ids):
        warnings.append(f"{len(ids) - checked} marketable items unresolved on {world}.")
    rows = []
    for group, snapshots in batches(api, region, sorted(matched), names):
        for iid in group:
            region_rows = snapshots[iid].listings if iid in snapshots else []
            if iid not in snapshots:
                warnings.append(f"Regional listings unresolved for item {iid}.")
            for own in matched[iid]:
                cheap = next((r for r in region_rows if r.hq == own.hq), None)
                rows.append(
                    RetainerItemRow(
                        item=ItemRef(
                            id=iid, name=(item_names or {}).get(iid, f"Item {iid}"), hq=own.hq
                        ),
                        their_ppu=own.ppu,
                        qty=own.qty,
                        data_age_h=max(0, clock() - own.last_review_ts) / 3600,
                        cheapest_world=cheap.world if cheap else None,
                        cheapest_dc=dcs.get(cheap.world) if cheap else None,
                        cheapest_ppu=cheap.ppu if cheap else None,
                        cheapest_qty=cheap.qty if cheap else None,
                        gap_pct=100 * (own.ppu - cheap.ppu) / own.ppu if cheap else None,
                        profit_qty=cheap.qty if cheap else None,
                        profit_per_unit=profit_per_unit(
                            own.ppu, cheap.ppu, seller_tax=0.05, buyer_tax=0.05,
                            travel_cost=costs.get(dcs.get(cheap.world, ""), 0), qty=cheap.qty,
                        )
                        if cheap
                        else None,
                    )
                )
    return RetainerScanResult(
        params=params,
        rows=rows,
        items_scanned=checked,
        duration_s=max(0, clock() - start),
        warnings=warnings,
    )
