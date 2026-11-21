from gilhound.market.http import HttpClient
from gilhound.models import ItemRef, ScanParams, ScanPreset

URL = "https://api.saddlebagexchange.com/api/scan"


def candidates(params: ScanParams, http: HttpClient) -> list[ItemRef]:
    preset = params.preset
    floor = {ScanPreset.FLIP: 1500, ScanPreset.HIGH_MARGIN: 5000}.get(preset, 3000)
    profit = 1000 if preset == ScanPreset.FLIP else floor
    windows = [(floor, profit, 24, 5, 40)]
    if preset != ScanPreset.FLIP:
        windows.append((floor, profit, 72, 3, 40))
    if preset == ScanPreset.SNIPE:
        windows.append((5000, 4000, 168, 3, 100))
    items = {}
    for hq in (False, True):
        for ppu, gain, hours, sales, roi in windows:
            # Keep insertion order stable for the existing POST replay keys.
            body = dict(
                preferred_roi=roi,
                min_profit_amount=params.min_profit or gain,
                min_desired_avg_ppu=params.min_ppu or ppu,
                min_stack_size=1,
                hours_ago=hours,
                min_sales=sales,
                hq=hq,
                home_server=params.home_world,
                filters=[0],
                region_wide=True,
                include_vendor=False,
                show_out_stock=False,
            )
            raw = http.request(URL, body)
            for row in raw.get("data", []) if isinstance(raw, dict) else raw:
                iid = int(row.get("item_id") or row["url"].rstrip("/").split("/")[-1])
                items[iid, hq] = ItemRef(id=iid, name=row.get("real_name") or str(iid), hq=hq)
    return list(items.values())
