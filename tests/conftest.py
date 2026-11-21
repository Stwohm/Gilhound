"""Shared fixtures for offline API tests."""

from collections.abc import Iterator
from secrets import token_urlsafe

import pytest
from fastapi.testclient import TestClient

from gilhound.api.app import create_app


@pytest.fixture
def token() -> str:
    return token_urlsafe()


@pytest.fixture
def client(token: str) -> Iterator[TestClient]:
    with TestClient(
        create_app(token),
        base_url="http://127.0.0.1",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client
