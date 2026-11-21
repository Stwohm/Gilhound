import base64
import json
import shutil
import subprocess
import sys
import time
import zlib
from pathlib import Path
from threading import Event

import pytest

from gilhound.cli import main
from gilhound.ingest.allagan_market import read
from gilhound.models import (
    ItemRef,
    Listing,
    PartnerRetainer,
    PartnerShare,
    RetainerItemRow,
    RetainerScanParams,
)
from gilhound.runtime import Runtime
from gilhound.runtime.partner import MAX_JSON, Sweep, collect, decode, encode
from gilhound.sources.fixtures import install

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/market/retainer"


def test_sweep_and_history(tmp_path: Path) -> None:
    rt = Runtime(tmp_path)
    try:
        settings = rt.settings.get()
        target = RetainerScanParams.model_validate_json((FIXTURE / "target.json").read_bytes())
        settings.partner_retainers = [PartnerRetainer(name=target.retainer_name.upper())]
        with install("replay", FIXTURE):
            sweep = collect(settings)
        assert {iid for iid, _ in sweep.stock} == {3, 9, 10, 11, 12}
        key = next(iter(sweep.stock))
        row = RetainerItemRow.model_construct(item=ItemRef(id=key[0], name="Sample", hq=key[1]))
        partners = rt.partners
        now = int(time.time())
        for day in range(5):
            for visit in range(2):
                partners.record(settings, sweep, now - (5 - day) * 86400 + visit)
        assert partners.status(settings)["stale"]
        for missed in range(3):
            partners.record(settings, Sweep({}, sweep.requested, sweep.requested), now - 3 + missed)
            partners.mark([row], settings)
            assert row.partner == "now"
        assert not partners.status(settings)["stale"]
        partners.record(settings, Sweep({}, sweep.requested, sweep.requested), now)
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
        return Sweep({}, {1}, {1})

    try:
        settings = runtime.settings.get()
        target = RetainerScanParams.model_validate_json((FIXTURE / "target.json").read_bytes())
        settings.partner_retainers = [PartnerRetainer(name=target.retainer_name)]
        runtime.settings.save(settings)
        monkeypatch.setattr("gilhound.runtime.partner.collect", fetch)
        runtime.partners.record(settings, Sweep({}, {1}, {1}), int(time.time()) - 3600)
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
        rt.partners.record(settings, Sweep({(1, True): (3, now - 120)}, {1}, {1}), now)
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


def test_import_preserves_second_clients_save(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    other = Runtime()
    try:
        share = PartnerShare(version=2, created_ts=1, sender="Sample", items=[])

        def interleave(code: str) -> PartnerShare:
            subprocess.run([sys.executable, "-c", (
                "from contextlib import closing\n"
                "from gilhound.runtime import Runtime\n"
                "with closing(Runtime()) as app:\n"
                "    settings = app.settings.get()\n"
                "    settings.home_world = 'Omega'\n"
                "    app.save_settings(settings)\n"
            )], check=True, timeout=15)
            return decode(code)

        monkeypatch.setattr("gilhound.cli.decode", interleave)
        assert main(["partner", "import", encode(share)]) == 0
        saved = other.settings.get()
        assert saved.partner_manual == share
        assert saved.home_world == "Omega"
    finally:
        other.close()


def test_app_patch_preserves_import_and_validates_merged_settings(client) -> None:
    snapshot = {"version": 2, "created_ts": 1, "sender": "Sample", "items": []}
    assert client.patch("/api/settings", json={"partner_manual": snapshot}).is_success
    saved = client.patch("/api/settings", json={"home_world": "Omega"}).json()
    assert saved["partner_manual"]["sender"] == "Sample" and saved["home_world"] == "Omega"
    assert client.patch("/api/settings", json={"partner_window_days": 1}).status_code == 422
    assert client.get("/api/settings").json() == saved


def test_partial_sweep_counts_only_resolved_absences(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    from gilhound.market.universalis import Snapshot, Universalis

    rt = Runtime(tmp_path, tmp_path / "plugins")
    try:
        settings = rt.settings.get()
        settings.partner_retainers = [PartnerRetainer(name="Sample")]
        now = int(time.time())
        monkeypatch.setattr(Universalis, "data_centers", lambda self: [
            SimpleNamespace(worlds=[SimpleNamespace(id=1, name=settings.home_world)])])
        monkeypatch.setattr(Universalis, "marketable", lambda self: [1, 2])
        listing = Listing(world=settings.home_world, retainer_name="Sample", retainer_hash="sample",
                          ppu=1, qty=1, hq=False, last_review_ts=now)
        snapshots = {i: Snapshot([listing], [], now, {}) for i in (1, 2)}
        monkeypatch.setattr(Universalis, "current", lambda *a, **k: snapshots)
        rt.partners.record(settings, collect(settings), now - 4000)
        snapshots = {2: Snapshot([], [], now, {})}
        for _ in range(4):
            rt.partners.record(settings, collect(settings), now)
        rows = [RetainerItemRow.model_construct(item=ItemRef(id=i, name="Sample", hq=False))
                for i in (1, 2)]
        rt.partners.mark(rows, settings)
        assert [r.partner for r in rows] == ["now", None]
        status = rt.partners.status(settings)
        assert status["stale"] and status["resolved_items"] == 1 and status["requested_items"] == 2
    finally:
        rt.close()
