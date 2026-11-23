import json
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from threading import Event
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from gilhound.api.app import create_app
from gilhound.cli import main
from gilhound.market.http import HttpClient, RateLimiter
from gilhound.market.items import SHEET, ItemCatalog
from gilhound.market.presets import _opportunities
from gilhound.market.search import XIV_SEARCH, resolve_item, run_search
from gilhound.market.universalis import BASE, Universalis
from gilhound.models import (
    ItemRef,
    SavedSearch,
    ScanParams,
    SearchEntry,
    SearchListChange,
    SearchName,
    SearchParams,
    SearchResolution,
)
from gilhound.runtime import Runtime
from gilhound.runtime.search_lists import SearchListStore
from gilhound.sources.fixtures import Session, install, request_key, write_json
from tools.anonymize import Anonymizer

NOW = 1_800_000_000
NAMES = {101: "Crimson Thread", 102: "Crimson Cloth", 103: "Bound Thread"}


def listing(world: int, ppu: int, qty: int, hq: bool) -> dict:
    return {
        "worldID": world, "pricePerUnit": ppu, "quantity": qty, "hq": hq,
        "lastReviewTime": NOW - 1800, "retainerName": "Synthetic seller",
    }


def sale(ppu: int, hq: bool, seconds_ago: int) -> dict:
    return {
        "pricePerUnit": ppu, "quantity": 1, "hq": hq,
        "timestamp": NOW - seconds_ago, "buyerName": "Synthetic buyer",
    }


def current_url(ids: str, region: str = "Europe", entries: int = 0, *, sell: bool = False) -> str:
    prefix = "items." if "," in ids else ""
    listing_fields = ("worldName", "worldID", "pricePerUnit", "quantity", "hq", "lastReviewTime")
    sale_fields = ("pricePerUnit", "quantity", "hq", "timestamp") if entries else ()
    if sell:
        listing_fields = ("retainerName", *listing_fields)
        sale_fields = (*sale_fields, "buyerName")
    fields = [f"{prefix}listings.{key}" for key in listing_fields]
    fields.extend(f"{prefix}recentHistory.{key}" for key in sale_fields)
    query = urlencode({"entries": entries, "fields": ",".join([*fields, "unresolvedItems"])})
    return f"{BASE}/{region}/{ids}?{query}"


@pytest.fixture
def replay(tmp_path: Path) -> Iterator[tuple[HttpClient, Session]]:
    """Synthetic market replies pass through the same anonymizer as recorded replies."""
    regional = [listing(2, 107, 4, True), listing(3, 71, 2, True),
                listing(2, 151, 8, True), listing(1, 41, 1, False)]
    marketable = [101, 102, *range(200, 301)]
    pages = {
        f"{BASE}/marketable": marketable,
        f"{BASE}/worlds": [{"id": 1, "name": "Cerberus"}, {"id": 2, "name": "Ragnarok"},
                           {"id": 3, "name": "Odin"}],
        f"{BASE}/data-centers": [{"name": "Chaos", "region": "Europe", "worlds": [1, 2]},
                                {"name": "Light", "region": "Europe", "worlds": [3]}],
        current_url("101"): {"listings": regional},
        current_url("101", "Cerberus", 200): {
            "listings": [listing(1, 201, 3, True), listing(1, 41, 1, False)],
            "recentHistory": [sale(241, True, 60), sale(251, True, 7200),
                              sale(7, False, 500), sale(21, True, 8 * 86400)],
        },
        current_url("102", "Cerberus", 200): {"listings": []},
        current_url("102", "Light"): {"listings": [], "unresolvedItems": [102]},
        f"{SHEET}?rows=101&fields=Icon": {
            "rows": [{"row_id": 101, "fields": {"Icon": {"path": "ui/icon/test.tex"}}}],
        },
    }
    sell_home = {
        "listings": [
            listing(1, 201, 3, True), listing(1, 220, 2, True),
            {**listing(1, 280, 10, True), "retainerName": "Synthetic seller two"},
            {**listing(1, 41, 1, False), "retainerName": "Synthetic seller three"},
        ],
        "recentHistory": [
            {**sale(241, True, 60), "quantity": 7},
            {**sale(251, True, 7200), "quantity": 7, "buyerName": "Synthetic buyer two"},
            {**sale(246, True, 86400), "quantity": 7},
            sale(7, False, 500), sale(21, True, 8 * 86400),
        ],
    }
    pages[current_url("101", "Cerberus", 200, sell=True)] = sell_home
    pages[f"{BASE}/Cerberus/101?listings=50&entries=100&hq=true"] = sell_home
    names = {1: "Cerberus", 2: "Ragnarok", 3: "Odin"}
    pages[f"{BASE}/Europe/101?listings=5&entries=0&hq=true"] = {
        "listings": [{**row, "worldName": names[row["worldID"]]} for row in regional],
    }
    history = f"{BASE}/history/Cerberus/101?entriesToReturn=80&entriesWithin=1209600&minSalePrice=0"
    pages[history] = {"entries": sell_home["recentHistory"]}
    group = ",".join(map(str, range(200, 300)))
    for ids in (group, "300"):
        empty = {"items": {iid: {"listings": []} for iid in ids.split(",")}}
        pages[current_url(ids)] = pages[current_url(ids, "Cerberus", 200)] = empty
    query = urlencode({
        "sheets": "Item", "fields": "Name", "limit": 100,
        "query": '+Name~"Thread" +IsUntradable=false',
    })
    pages[f"{XIV_SEARCH}?{query}"] = {"results": [
        {"row_id": iid, "fields": {"Name": NAMES[iid]}} for iid in (101, 103)
    ]}
    anonymizer = Anonymizer(b"synthetic-search-fixture", [])
    requests = {}
    for url, payload in pages.items():
        key = request_key("GET", url)
        write_json(tmp_path / f"{key}.json", anonymizer.market(payload))
        requests[key] = {"method": "GET", "url": url}
    write_json(tmp_path / "index.json", {"timestamp": NOW, "requests": requests})
    client = HttpClient(limiter=RateLimiter(1_000_000), attempts=1)
    with install("replay", tmp_path) as session:
        yield client, session
        assert not session.unanswered


def test_search_cheapest_tax_home_and_quantity(replay: tuple[HttpClient, Session]) -> None:
    http, session = replay
    progress = []
    result = run_search(
        SearchParams(entries=[
            SearchEntry(item_id=101, hq_only=True, quantity=5),
            SearchEntry(item_id=101, hq_only=True, quantity=20),
            SearchEntry(item_id=101),
            SearchEntry(item_id=1234),
        ], top=2),
        http=http, clock=session.now, progress=lambda done, total: progress.append((done, total)),
        travel_cost={"Chaos": 100, "Light": 500},
    )
    row, partial, mixed, unknown = result.rows
    assert [listing.ppu for listing in row.listings] == [71, 107]
    assert all(listing.hq and listing.data_age_h == 0.5 for listing in row.listings)
    assert all(listing.last_review_ts == NOW - 1800 for listing in row.listings)
    assert row.listings[0].dc == "Light"
    assert [listing.total for listing in row.listings] == [149, 449]
    assert row.home_cheapest and row.home_cheapest.ppu == 201
    assert row.home_median_7d == 246
    assert row.home_sale_age_h == pytest.approx(1 / 60)
    assert row.cover and row.cover.covered == 6 and row.cover.requested == 5
    assert row.cover.worlds == ["Odin", "Ragnarok"]
    assert row.cover.purchase_cost == 598 and row.cover.travel_cost == 600
    assert row.cover.total_cost == 1198
    assert partial.cover and partial.cover.covered == 14 and len(partial.cover.listings) == 3
    assert partial.cover.travel_cost == 600
    assert mixed.listings[0].ppu == 41 and not mixed.listings[0].hq
    assert mixed.home_median_7d == 241
    assert unknown.error and unknown.item is None
    assert "retainer" not in result.model_dump_json().casefold()
    assert progress[0] == (0, 8) and progress[-1] == (8, 8)
    assert progress == sorted(progress)


def test_batching_and_case_insensitive_home_region(replay: tuple[HttpClient, Session]) -> None:
    http, session = replay
    result = run_search(
        SearchParams(entries=[SearchEntry(item_id=iid) for iid in range(200, 301)]),
        http=http, clock=session.now,
    )
    assert len(result.rows) == 101 and all(not row.error for row in result.rows)
    assert len(session.used) == 7  # Marketable IDs, metadata, and two batches per location.
    single = run_search(SearchParams(region="cerberus", entries=[SearchEntry(item_id=101)]),
                        http=http, clock=session.now)
    assert single.region == "Cerberus"
    assert single.rows[0].listings[0] == single.rows[0].home_cheapest


def test_unresolved_listing_is_per_item(replay: tuple[HttpClient, Session]) -> None:
    http, session = replay
    result = run_search(SearchParams(region="Light", entries=[SearchEntry(item_id=102)]),
                        http=http, clock=session.now)
    assert result.rows[0].item and result.rows[0].error


def test_resolve_exact_prefix_ambiguous_and_unmarketable(
    replay: tuple[HttpClient, Session], tmp_path: Path,
) -> None:
    http, session = replay
    write_json(tmp_path / "items.json", NAMES)
    with closing(ItemCatalog(tmp_path, http)) as catalog:
        ids = catalog.marketable()

        def resolve(query: str) -> SearchResolution:
            return resolve_item(
                SearchName(name=query), marketable=ids, names=catalog.names, http=http,
            )

        exact = resolve("  CRIMSON THREAD  ")
        prefix = resolve("crimson clo")
        assert exact.item and exact.item.id == 101
        assert prefix.item and prefix.item.id == 102
        assert [item.id for item in resolve("crimson").candidates] == [102, 101]
        assert resolve("Bound Thread").error == "This item is unmarketable."
        numeric = resolve("102")
        assert resolve("103").error and numeric.item and numeric.item.id == 102
        assert len(session.used) == 1
        remote = resolve("Thread")
        assert remote.item and remote.item.id == 101
        assert len(session.used) == 2


def test_search_job_returns_decorated_replay_result(
    replay: tuple[HttpClient, Session], tmp_path: Path, token: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    http, _ = replay
    root = tmp_path / "state"
    root.mkdir()
    write_json(root / "items.json", NAMES)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    runtime = Runtime(root, tmp_path / "plugins")
    runtime.items.http = http
    app = create_app(token, runtime, schedule=False)
    with TestClient(app, base_url="http://127.0.0.1",
                    headers={"Authorization": f"Bearer {token}"}) as client:
        assert client.get("/api/search/regions").json() == [
            "Europe", "Chaos", "Light", "Cerberus", "Odin", "Ragnarok",
        ]
        response = client.post("/api/jobs/search", json={"entries": [{"name": "Crimson Thr"}]})
        assert response.status_code == 202
        endpoint = f"/api/jobs/{response.json()['id']}"
        for _ in range(250):
            job = client.get(endpoint).json()
            if job["state"] in ("complete", "failed"):
                break
            Event().wait(0.01)
        assert job["state"] == "complete", job
        assert job["done"] == job["total"] == 2
        item = job["result"]["rows"][0]["item"]
        assert item["name"] == "Crimson Thread"
        assert item["icon_url"].startswith("https://v2.xivapi.com/api/asset?")
        assert item["universalis_url"].endswith("?server=Europe")
        assert item["saddlebag_url"].endswith("/101")


def test_cli_search_uses_saved_region_and_flags(
    replay: tuple[HttpClient, Session], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    root = tmp_path / "Gilhound"
    root.mkdir()
    write_json(root / "items.json", NAMES)
    SearchListStore(root).change(SearchListChange(
        action="create", name="Restock",
        search=SavedSearch(region="Cerberus", entries=[SearchEntry(item_id=101, quantity=1)]),
    ))
    assert main(["search-lists", "--json"]) == 0
    library = json.loads(capsys.readouterr().out)
    assert library["rows"][0]["name"] == "Restock"
    assert main(["search", "--list", "Restock", "--hq", "--qty", "3", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["region"] == "Cerberus"
    assert result["rows"][0]["entry"]["quantity"] == 3
    assert result["rows"][0]["listings"][0]["hq"]


def test_sell_matches_flip_and_prices_quantity_cover(replay: tuple[HttpClient, Session]) -> None:
    http, session = replay
    costs = {"Light": 40, "Chaos": 10}
    entry = SearchEntry(item_id=101, hq_only=True, quantity=5)
    params = SearchParams(mode="sell", entries=[entry], top=1)
    result = run_search(params, http=http, clock=session.now, travel_cost=costs)
    row = result.rows[0]
    sell = row.sell
    assert sell and row.cover
    scan_params = ScanParams(
        home_world="Cerberus", buy_region="Europe", seller_tax=0.05, travel_cost=costs,
    )
    [(_, scan)] = _opportunities(
        ItemRef(id=101, name=NAMES[101], hq=True), scan_params, Universalis(http),
        session.now(), {"Odin": "Light", "Ragnarok": "Chaos"}, legacy=False,
    )
    assert sell.sell_ppu == scan.home_median_7d == 246
    for field in (
        "volume_proof", "units_sold_7d", "sales_7d", "home_lowest", "ask_exit_ppu",
        "distinct_sellers", "home_listing_count", "rival_stock", "buy_world", "buy_ppu",
        "profit_per_unit", "roi_pct", "days_to_sell", "est_wait_days", "data_age_h",
    ):
        assert getattr(sell, field) == getattr(scan, field), field
    assert sell.distinct_sellers == sell.volume_proof.distinct_buyers_7d == 2
    assert sell.volume_proof.top_buyer_share == pytest.approx(2 / 3)
    assert sell.home_listing_count == 3
    assert sell.rival_stock == 5
    assert row.cover.covered == 6 and row.cover.total_cost == 648
    cover_gain = 246 * 0.95 * 6 - 648
    assert sell.cover_profit == pytest.approx(cover_gain)
    assert sell.cover_roi_pct == pytest.approx(100 * cover_gain / 648)
    assert "retainer" not in result.model_dump_json().casefold()
    assert "buyer_hash" not in result.model_dump_json()


def test_sell_home_region_has_no_buy_basis(replay: tuple[HttpClient, Session]) -> None:
    http, session = replay
    result = run_search(
        SearchParams(
            mode="sell", region="cerberus", entries=[SearchEntry(item_id=101, quantity=1)],
        ),
        http=http, clock=session.now,
    )
    row = result.rows[0]
    assert row.sell and row.sell.sell_ppu == 243.5
    assert row.cover and row.cover.covered == 1
    for field in (
        "buy_world", "buy_ppu", "profit_per_unit", "roi_pct", "days_to_sell", "est_wait_days",
        "data_age_h", "cover_profit", "cover_roi_pct",
    ):
        assert getattr(row.sell, field) is None
    assert row.warnings == ["No regional buy listing outside the home world"]


def test_sell_without_recent_sales_keeps_off_world_quote(
    replay: tuple[HttpClient, Session],
) -> None:
    http, _ = replay
    result = run_search(
        SearchParams(mode="sell", entries=[SearchEntry(item_id=101)], top=1),
        http=http, clock=lambda: NOW + 8 * 86400,
    )
    row = result.rows[0]
    assert row.sell and not row.error
    assert row.listings[0].world == "Cerberus"
    assert row.sell.buy_world == "Odin" and row.sell.buy_ppu == 71
    assert row.warnings == ["No home sales in 7 days"]
    assert row.sell.sell_ppu is row.sell.profit_per_unit is row.sell.days_to_sell is None
    assert row.sell.units_sold_7d == row.sell.sales_7d == row.sell.rival_stock == 0
    assert row.sell.volume_proof.model_dump() == {
        "units_per_day": 0.0, "distinct_buyers_7d": 0, "days_with_sales": 0,
        "top_buyer_share": 0.0, "last_sale_age_h": None,
    }


def test_cli_sell_mode_saved_default_and_explicit_buy(
    replay: tuple[HttpClient, Session], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    root = tmp_path / "Gilhound"
    root.mkdir()
    write_json(root / "items.json", NAMES)
    SearchListStore(root).change(SearchListChange(
        action="create", name="Sell",
        search=SavedSearch(mode="sell", entries=[SearchEntry(item_id=101)]),
    ))
    for flags in (["101", "--mode", "sell"], ["--list", "Sell"]):
        assert main(["search", *flags, "--json"]) == 0
        row = json.loads(capsys.readouterr().out)["rows"][0]
        assert row["sell"]["buy_world"] == "Odin"
    assert main(["search", "--list", "Sell", "--mode", "buy", "--json"]) == 0
    assert "sell" not in json.loads(capsys.readouterr().out)["rows"][0]
