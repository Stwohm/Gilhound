"""Verify stub behavior, credential handling and the exported schema."""

from pathlib import Path
from secrets import token_urlsafe

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gilhound.api.app import create_app
from gilhound.api.export_openapi import REPO_ROOT, export_openapi
from gilhound.models import UserSettings

SETTINGS = {"home_world": "Cerberus", "buy_region": "Europe"}


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST:5173", "127.0.0.1", "127.0.0.1:65535"])
def test_accepts_local_host(client: TestClient, host: str) -> None:
    assert client.get("/api/info", headers={"Host": host}).status_code == 501


@pytest.mark.parametrize(
    "host",
    [
        "",
        "testserver",
        "localhost.example.invalid",
        "127.0.0.1:0",
        "127.0.0.1:65536",
        "127.0.0.1:abc",
        "127.0.0.1:80:443",
        "127.0.0.2",
        "[::1]",
    ],
)
def test_rejects_invalid_host(client: TestClient, host: str) -> None:
    assert client.get("/api/info", headers={"Host": host}).status_code == 400


def test_rejects_multiple_host_headers(client: TestClient) -> None:
    response = client.get("/api/info", headers=[("Host", "localhost"), ("Host", "127.0.0.1")])
    assert response.status_code == 400


@pytest.mark.parametrize("authorization", [None, "Basic dummy", "Bearer", "Bearer dummy"])
def test_requires_session_token(token: str, authorization: str | None) -> None:
    headers = {"Authorization": authorization} if authorization is not None else {}
    with TestClient(create_app(token), base_url="http://localhost") as client:
        response = client.get("/api/info", headers=headers)
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"


def test_accepts_case_insensitive_bearer_scheme(client: TestClient, token: str) -> None:
    response = client.get("/api/info", headers={"Authorization": f"bEaReR {token}"})
    assert response.status_code == 501


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/info", None),
        ("GET", "/api/settings", None),
        ("PUT", "/api/settings", SETTINGS),
        ("POST", "/api/scan", {**SETTINGS, "seller_tax": 0}),
        ("POST", "/api/retainer-scan", {"retainer_name": "Synthetic Retainer"}),
        ("GET", "/api/snipes", None),
        ("POST", "/api/snipes/run", None),
        ("GET", "/api/sources", None),
        ("GET", "/api/positions", None),
        ("GET", "/api/pnl", None),
        ("POST", "/api/diagnostics/export", None),
    ],
)
def test_endpoints_are_stubs(
    client: TestClient, method: str, path: str, body: dict[str, object] | None
) -> None:
    response = client.request(method, path, json=body)
    assert response.status_code == 501
    assert response.json() == {"detail": "Not implemented"}


def test_webhook_is_masked_by_response_model() -> None:
    # Use an echo endpoint to exercise a successful response while settings is a stub.
    app = FastAPI()

    @app.put("/settings")
    async def echo_settings(settings: UserSettings) -> UserSettings:
        return settings

    value = token_urlsafe()
    with TestClient(app) as client:
        response = client.put("/settings", json={**SETTINGS, "webhook_url": value})
    assert response.status_code == 200
    assert response.json()["webhook_url"] == "**********"
    assert value not in response.text
    assert UserSettings(**SETTINGS).model_dump()["webhook_url"] is None


def test_invalid_settings_does_not_echo_webhook(client: TestClient) -> None:
    value = token_urlsafe()
    response = client.put("/api/settings", json={"buy_region": "Europe", "webhook_url": value})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "home_world"]
    assert value not in response.text


def test_openapi_export_is_current(tmp_path: Path) -> None:
    exported = tmp_path / "openapi.json"
    export_openapi(exported)
    assert exported.read_text(encoding="utf-8") == (REPO_ROOT / "openapi.json").read_text(
        encoding="utf-8"
    )
