"""Shared fixtures for offline API tests."""

import json
import shutil
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from secrets import token_urlsafe

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gilhound.api.app import create_app
from gilhound.runtime import Runtime
from gilhound.sources.fixtures import request_key


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*args: object, **kwargs: object) -> None:
        raise OSError("Offline test transport")

    monkeypatch.setattr(urllib.request, "urlopen", unavailable)


@pytest.fixture
def extended_replay(tmp_path: Path) -> Callable[[Path], Path]:
    """Serve the anonymised legacy samples under the new 14-day request URLs."""

    def build(source: Path) -> Path:
        dest = tmp_path / source.name
        shutil.copytree(source, dest)
        index = json.loads((dest / "index.json").read_bytes())
        requests = index["requests"]
        flip_sales = {}
        for key, meta in list(requests.items()):
            url = meta["url"]
            if "entriesWithin=604800" in url:
                url = url.replace("entriesWithin=604800", "entriesWithin=1209600")
                new = request_key("GET", url)
                (dest / f"{key}.json").rename(dest / f"{new}.json")
                del requests[key]
                requests[new] = {"method": "GET", "url": url}
            elif source.name == "flip_scan" and "listings=50&entries=100" in url:
                path = url.split("?")[0].replace("/v2/", "/v2/history/")
                payload = json.loads((dest / f"{key}.json").read_bytes())
                flip_sales.setdefault(path, []).extend(payload.get("recentHistory", []))
        for path, sales in flip_sales.items():
            url = path + "?entriesToReturn=80&entriesWithin=1209600&minSalePrice=0"
            key = request_key("GET", url)
            requests[key] = {"method": "GET", "url": url}
            (dest / f"{key}.json").write_text(json.dumps({"entries": sales}))
        (dest / "index.json").write_text(json.dumps(index))
        return dest

    return build


@pytest.fixture
def token() -> str:
    return token_urlsafe()


@pytest.fixture
def app(token: str, tmp_path: Path) -> FastAPI:
    return create_app(token, Runtime(tmp_path / "state", tmp_path / "plugins"), schedule=False)


@pytest.fixture
def client(token: str, app: FastAPI) -> Iterator[TestClient]:
    with TestClient(
        app,
        base_url="http://127.0.0.1",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client
