"""Shared fixtures for offline API tests."""

from collections.abc import Iterator
from pathlib import Path
from secrets import token_urlsafe

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gilhound.api.app import create_app
from gilhound.runtime import Runtime


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
