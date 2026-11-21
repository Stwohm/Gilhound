"""Offline acceptance tests for the captured legacy market scans."""

import io
import re
import sys
import time
import urllib.request
from decimal import Decimal
from pathlib import Path
from secrets import token_bytes
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gilhound.sources.fixtures import UnknownRequest, install, request_key
from tools.anonymize import Anonymizer
from tools.record_fixtures import GOLDEN, MARKET, SCRIPTS, SKIPPED, run_scan


@pytest.mark.parametrize("script", SCRIPTS)
def test_golden(script: str) -> None:
    with patch.object(
        urllib.request, "urlopen", side_effect=AssertionError("Network used in replay")
    ):
        actual = run_scan(script, "replay", MARKET / script)
    assert actual == (GOLDEN / f"{script}.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("script", SKIPPED)
def test_personal_scan(script: str) -> None:
    pytest.skip(f"{script} has a hard-coded personal item selection")


def test_market_anonymization_preserves_buyer_groups() -> None:
    anonymizer = Anonymizer(token_bytes(32), [re.compile("synthetic")])
    payload = [
        {"buyerName": "example-one", "pricePerUnit": 350, "quantity": 2},
        {"buyerName": "example-one", "comment": "synthetic-note"},
        {"buyerName": "example-two", "retainerID": 1234},
    ]
    clean = anonymizer.market(payload)
    assert clean[0]["buyerName"] == clean[1]["buyerName"] != clean[2]["buyerName"]
    assert clean[0]["buyerName"].startswith("anon_")
    assert clean[1]["comment"].startswith("anon_")
    assert clean[2]["retainerID"].startswith("anon_")
    assert clean[0]["pricePerUnit"] == 350 and clean[0]["quantity"] == 2
    assert anonymizer.market(payload, scale=Decimal(2))[0]["pricePerUnit"] == 700


def test_record_and_replay_distinguish_request_bodies(tmp_path: Path) -> None:
    url = "https://example.invalid/scan"
    first = urllib.request.Request(url, data=b'{"hq":false}')
    second = urllib.request.Request(url, data=b'{"hq":true}')
    responses = [io.BytesIO(b'{"data":[1]}'), io.BytesIO(b'{"data":[2]}')]
    with patch.object(urllib.request, "urlopen", side_effect=responses) as transport:
        with install("record", tmp_path):
            timestamp = time.time()
            assert urllib.request.urlopen(first).read() == b'{"data":[1]}\n'
            assert urllib.request.urlopen(second).read() == b'{"data":[2]}\n'
            assert urllib.request.urlopen(first).read() == b'{"data":[1]}\n'
        assert transport.call_count == 2
    with (
        patch.object(
            urllib.request,
            "urlopen",
            side_effect=AssertionError("Network used in replay"),
        ),
        install("replay", tmp_path) as replay,
    ):
        assert time.time() == timestamp
        assert urllib.request.urlopen(second).read() == b'{"data":[2]}\n'
        with pytest.raises(UnknownRequest):
            urllib.request.urlopen(url)
        assert replay.unanswered == {request_key("GET", url)}
