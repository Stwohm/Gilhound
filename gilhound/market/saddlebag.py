from gilhound.market.http import HttpClient
from gilhound.models import ItemRef, ScanParams

URL = "https://api.saddlebagexchange.com/api/scan"


def candidates(params: ScanParams, http: HttpClient) -> list[ItemRef]:
    items = {}
    for hq in (False, True):
        if params.quality != "both" and hq != (params.quality == "hq"):
            continue
        for window in params.windows:
            # Keep insertion order stable for the existing POST replay keys.
            body = dict(
                preferred_roi=window.preferred_roi,
                min_profit_amount=window.min_profit_amount,
                min_desired_avg_ppu=window.min_desired_avg_ppu,
                min_stack_size=params.min_stack_size,
                hours_ago=window.hours_ago,
                min_sales=window.min_sales,
                hq=hq,
                home_server=params.home_world,
                filters=params.filters,
                region_wide=params.region_wide,
                include_vendor=params.include_vendor,
                show_out_stock=params.show_out_stock,
            )
            raw = http.request(URL, body)
            for row in raw.get("data", []) if isinstance(raw, dict) else raw:
                iid = int(row.get("item_id") or row["url"].rstrip("/").split("/")[-1])
                items[iid, hq] = ItemRef(id=iid, name=row.get("real_name") or str(iid), hq=hq)
    return list(items.values())
