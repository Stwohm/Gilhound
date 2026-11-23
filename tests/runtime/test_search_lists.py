from pathlib import Path

from fastapi.testclient import TestClient

from gilhound.runtime.search_lists import SearchListStore
from gilhound.runtime.store import SettingsStore


def test_list_crud_and_corruption(client: TestClient, tmp_path: Path) -> None:
    path = tmp_path / "state" / "search-lists.json"
    store = SearchListStore(path.parent)
    endpoint = "/api/search-lists"
    search = {"region": "Light", "entries": [{"item_id": 101, "hq_only": True, "quantity": 12}]}

    assert client.get(endpoint).json() == {"lists": {}, "warning": None}
    assert client.post(endpoint, json={
        "action": "create", "name": " Weekly ", "search": search,
    }).json()["lists"] == {"Weekly": search}
    assert client.post(endpoint, json={
        "action": "create", "name": "weekly", "search": search,
    }).status_code == 400
    assert store.get("weekly").model_dump() == search

    edited = {**search, "region": "Chaos"}
    assert client.post(endpoint, json={
        "action": "save", "name": "weekly", "search": edited,
    }).json()["lists"] == {"Weekly": edited}
    assert client.post(endpoint, json={
        "action": "rename", "name": "Weekly", "new_name": "Restock",
    }).json()["lists"] == {"Restock": edited}
    assert client.post(endpoint, json={
        "action": "delete", "name": "Restock",
    }).json()["lists"] == {}
    assert client.post(endpoint, json={
        "action": "delete", "name": "Restock",
    }).status_code == 404

    path.write_text("invalid JSON", encoding="utf-8")
    corrupt = client.get(endpoint).json()
    assert corrupt["lists"] == {} and "Could not read" in corrupt["warning"]
    assert client.post(endpoint, json={
        "action": "create", "name": "Recovered", "search": search,
    }).json() == {"lists": {"Recovered": search}, "warning": None}


def test_column_orders_are_independent_and_persistent(client: TestClient, tmp_path: Path) -> None:
    endpoint = "/api/column-order"
    assert client.get(endpoint).json() == {}
    assert client.put(endpoint, json={"table": "scan", "order": ["Price", "Item"]}).json() == [
        "Price", "Item",
    ]
    saved = client.put(endpoint, json={"table": "sales", "order": ["Units", "Item"]})
    assert saved.status_code == 200
    assert client.patch("/api/settings", json={"home_world": "Odin"}).status_code == 200
    assert SettingsStore(tmp_path / "state" / "settings.json").get().column_order == {
        "scan": ["Price", "Item"], "sales": ["Units", "Item"],
    }
    assert client.put(endpoint, json={"table": "scan", "order": []}).json() == []
    assert client.get(endpoint).json() == {"sales": ["Units", "Item"]}


def test_search_list_mode_round_trip_and_legacy_default(client: TestClient, tmp_path: Path) -> None:
    store = SearchListStore(tmp_path / "state")
    store.path.write_text('{"lists":{"Legacy":{"entries":[{"item_id":101}]}}}', encoding="utf-8")
    assert store.get("Legacy").mode == "buy"
    endpoint = "/api/search-lists"
    response = client.post(endpoint, json={
        "action": "create", "name": "Sell",
        "search": {"entries": [{"item_id": 101}], "mode": "sell"},
    })
    assert response.status_code == 200
    assert response.json()["lists"]["Sell"]["mode"] == "sell"
    assert store.get("Sell").mode == "sell"
    assert store.get("Legacy").mode == "buy"


def test_search_mode_setting_persists_alongside_column_order(
    client: TestClient, tmp_path: Path,
) -> None:
    assert client.get("/api/settings").json()["search_mode"] == "buy"
    assert client.patch("/api/settings", json={"search_mode": "sell"}).status_code == 200
    assert client.put("/api/column-order", json={
        "table": "search-sell", "order": ["Sell price", "Item"],
    }).status_code == 200
    saved = SettingsStore(tmp_path / "state" / "settings.json").get()
    assert saved.search_mode == "sell"
    assert saved.column_order == {"search-sell": ["Sell price", "Item"]}
