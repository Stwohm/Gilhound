import base64
import json
import shutil
import time
import zlib
from pathlib import Path
from threading import Event

import pytest

from gilhound.cli import main
from gilhound.ingest.allagan_market import read
from gilhound.models import (
    ItemRef,
    PartnerRetainer,
    PartnerShare,
    RetainerItemRow,
    RetainerScanParams,
)
from gilhound.runtime import Runtime
from gilhound.runtime.partner import MAX_JSON, collect, decode, encode
from gilhound.sources.fixtures import install

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/market/retainer"


def test_sweep_and_history(tmp_path: Path) -> None:
    rt = Runtime(tmp_path)
    try:
        settings = rt.settings.get()
        target = RetainerScanParams.model_validate_json((FIXTURE / "target.json").read_bytes())
        settings.partner_retainers = [PartnerRetainer(name=target.retainer_name.upper())]
        with install("replay", FIXTURE):
            stock = collect(settings)
        assert {iid for iid, _ in stock} == {3, 9, 10, 11, 12}
        key = next(iter(stock))
        row = RetainerItemRow.model_construct(item=ItemRef(id=key[0], name="Sample", hq=key[1]))
        partners = rt.partners
        now = int(time.time())
        for day in range(5):
            for visit in range(2):
                partners.record(settings, stock, now - (5 - day) * 86400 + visit)
        assert partners.status(settings)["stale"]
        for missed in range(3):
            partners.record(settings, {}, now - 3 + missed)
            partners.mark([row], settings)
            assert row.partner == "now"
        assert not partners.status(settings)["stale"]
        partners.record(settings, {}, now)
        partners.mark([row], settings)
        assert row.partner == "regular" and "5 of 14" in row.partner_detail
        settings.partner_regular_days = 6
        partners.mark([row], settings)
        assert row.partner is None
    finally:
        rt.close()


def test_pending_sweep_reuses_cache_without_waiting(tmp_path, monkeypatch) -> None:
    runtime = Runtime(tmp_path, tmp_path / "plugins")
    release = Event()
    calls = []

    def fetch(settings):
        calls.append(1)
        release.wait(5)
        return {}

    try:
        settings = runtime.settings.get()
        target = RetainerScanParams.model_validate_json((FIXTURE / "target.json").read_bytes())
        settings.partner_retainers = [PartnerRetainer(name=target.retainer_name)]
        runtime.settings.save(settings)
        monkeypatch.setattr("gilhound.runtime.partner.collect", fetch)
        runtime.partners.record(settings, {}, int(time.time()) - 3600)
        pending = runtime.partners.refresh()
        assert pending is not None and not pending.done()
        assert runtime.partners.refresh() is pending
        assert runtime.partners.status(settings)["age_min"] == 60
        release.set()
        pending.result(timeout=2)
        assert runtime.partners.refresh() is None and calls == [1]
    finally:
        release.set()
        runtime.close()


def test_cli_reimport_replaces_manual_list(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert main(["partner", "share"]) == 0
    empty = json.loads(capsys.readouterr().out)["code"]
    share = decode(empty)
    share = PartnerShare.model_validate({**share.model_dump(), "items": [{"id": 9, "hq": True}]})
    assert main(["partner", "import", encode(share), "--preview"]) == 0
    assert json.loads(capsys.readouterr().out)["adds"] == [[9, True]]
    assert main(["partner", "import", encode(share)]) == 0
    runtime = Runtime()
    try:
        row = RetainerItemRow.model_construct(item=ItemRef(id=9, name="Sample", hq=True))
        runtime.partners.mark([row], runtime.settings.get())
        assert row.partner == "manual" and "shared" in row.partner_detail
        assert main(["partner", "import", empty]) == 0
        runtime.partners.mark([row], runtime.settings.get())
        assert row.partner is None
    finally:
        runtime.close()


def test_share_roundtrip_and_limits() -> None:
    share = PartnerShare.model_validate(
        dict(
            version=1,
            sender="Sample",
            created_ts=1,
            items=[{"id": 1, "hq": True, "note": "Sample"}],
        )
    )
    assert decode(encode(share)) == share
    for raw in (b"{}", b'{"version":2}', b" " * (MAX_JSON + 1)):
        code = "GH1." + base64.urlsafe_b64encode(zlib.compress(raw)).decode().rstrip("=")
        with pytest.raises(ValueError):
            decode(code)
    for junk in ("", "GH2.abcdef", "GH1.A", "GH1." + "a" * 256000):
        with pytest.raises(ValueError):
            decode(junk)


def test_plugin_share_api_and_cli(client, tmp_path, monkeypatch, capsys) -> None:
    plugins = tmp_path / "plugins"
    shutil.copytree(FIXTURE.parents[1] / "plugins/AllaganMarket", plugins / "AllaganMarket")
    draft = {"sender": "Sample", "items": [{"id": 9, "hq": True}]}
    code = client.post("/api/partner/share", json=draft).json()
    assert code.startswith("GH2.")
    preview = client.post("/api/partner/preview", json={"code": code}).json()
    share = decode(code)
    assert share == PartnerShare.model_validate(preview)
    assert share.listings and share.items[0].id == 9
    stocks = read(plugins / "AllaganMarket").records.listings
    assert [s.model_dump() for s in share.listings] == [
        {"id": s.item.id, "hq": s.item.hq, "qty": s.qty, "ppu": s.ppu} for s in stocks
    ]
    too_many = [{"id": i + 1, "hq": False} for i in range(200)]
    response = client.post("/api/partner/share", json={**draft, "items": too_many})
    assert response.status_code == 422 and "200" in response.json()["detail"]
    monkeypatch.setenv("APPDATA", str(tmp_path))
    shutil.copytree(plugins, tmp_path / "XIVLauncher/pluginConfigs")
    assert main(["partner", "share"]) == 0
    assert decode(json.loads(capsys.readouterr().out)["code"]).listings == share.listings
    assert main(["partner", "import", code]) == 0
    capsys.readouterr()
    assert main(["partner", "status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["snapshot_age_min"] == 0 and status["active_listings"] == len(share.listings)
    assert status["avoid_items"] == 1
    old = {**draft, "version": 1, "created_ts": 1}
    compressed = zlib.compress(json.dumps(old).encode())
    legacy = "GH1." + base64.urlsafe_b64encode(compressed).decode().rstrip("=")
    assert main(["partner", "import", legacy]) == 0
    mismatch = client.post("/api/partner/preview", json={"code": "GH2." + legacy[4:]})
    assert mismatch.status_code == 422
    capsys.readouterr()
    assert main(["partner", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["active_listings"] == 0


def test_shared_badge_sources_and_staleness(tmp_path: Path) -> None:
    rt = Runtime(tmp_path, tmp_path / "plugins")
    try:
        settings = rt.settings.get()
        now = int(time.time())
        settings.partner_regular_days = 1
        settings.partner_manual = PartnerShare.model_validate({
            "version": 2, "created_ts": now - 49 * 3600, "sender": "Sample",
            "items": [{"id": 1, "hq": True}],
            "listings": [{"id": 1, "hq": True, "qty": 3, "ppu": 12}],
        })
        row = RetainerItemRow.model_construct(item=ItemRef(id=1, name="Sample", hq=True))
        rt.partners.mark([row], settings)
        assert row.partner == "shared" and "stale" in row.partner_detail
        settings.partner_snapshot_hours = 50
        rt.partners.record(settings, {(1, True): (3, now - 120)}, now)
        rt.partners.mark([row], settings)
        assert row.partner_detail.splitlines() == [
            "Partner listing (shared 49 h ago)",
            "Partner listing seen on Universalis 2 min ago (3 units)",
            "Partner regular: seen 1 of 14 days",
            "Partner asked to avoid (shared 49 h ago)",
        ]
        row.item.hq = False
        rt.partners.mark([row], settings)
        assert row.partner is None
    finally:
        rt.close()
