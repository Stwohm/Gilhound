import json
from pathlib import Path

import pytest

from gilhound import cli
from gilhound.ingest.common import Records, Source, event, item_ref
from gilhound.models import LedgerEventKind, ScanParams, ScanResult, SourceName, SourceStatus
from gilhound.runtime import Runtime
from gilhound.runtime.profiles import ProfileChange


def test_profiles_lifecycle_and_upgrade(tmp_path: Path) -> None:
    runtime = Runtime(tmp_path, tmp_path / "plugins")
    try:
        store = runtime.profiles
        settings = runtime.settings.get()
        original = store.list("scan", settings)
        profile = original.profiles["Volume"].model_copy(deep=True)
        profile.params.windows[0].hours_ago = 12
        profile.params.min_units_day = 10

        def change(action: str, name: str, **kwargs: object) -> None:
            store.change("scan", ProfileChange(action=action, name=name, **kwargs), settings)

        change("save", "Volume", profile=profile)
        change("create", "My volume", profile=profile)
        profile.params.windows[0].hours_ago = 6
        change("save", "Volume", profile=profile)
        assert store.list("scan", settings).profiles["My volume"].params.windows[0].hours_ago == 12
        change("rename", "My volume", new_name="Fast")
        change("reset", "Volume")
        with pytest.raises(ValueError):
            change("delete", "Volume")
        with pytest.raises(ValueError):
            change("create", "fast", profile=profile)
        data = json.loads(store.path.read_bytes())
        assert data["version"] == 1
        params = data["scan"]["Fast"]["params"]
        del params["min_days_with_sales"]
        params["future_option"] = True
        store.path.write_text(json.dumps(data))
        restored = store.list("scan", settings).profiles
        assert restored["Fast"].params.min_days_with_sales == 4
        assert restored["Fast"].params.windows[0].hours_ago == 12
        assert restored["Volume"] == original.profiles["Volume"]
        change("delete", "Fast")
        assert store.list("scan", settings) == original
    finally:
        runtime.close()


def test_next_run_reads_active_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = Runtime(tmp_path, tmp_path / "plugins")
    captured = []

    def scan(params: ScanParams, **kwargs: object) -> ScanResult:
        captured.append(params)
        return ScanResult(params=params, rows=[], started_ts=1, finished_ts=1)

    monkeypatch.setattr("gilhound.runtime.run_scan", scan)
    try:
        settings = runtime.settings.get()
        profile = runtime.profiles.list("snipes", settings).profiles["Snipe"]
        runtime.run_snipes()
        profile.params.windows[1].preferred_roi = 15
        profile.params.snipe_rule.min_profit = 123
        profile.params.freshness_tiers[0].max_age_h = 0.5
        runtime.profiles.change(
            "snipes", ProfileChange(action="create", name="Custom", profile=profile), settings
        )
        runtime.run_snipes()
        assert captured[1] == profile.params and captured[0] != captured[1]
        state = runtime.profiles.change(
            "snipes", ProfileChange(action="rename", name="Custom", new_name="Renamed"), settings
        )
        assert state.active == "Renamed"
        state = runtime.profiles.change(
            "snipes", ProfileChange(action="delete", name="Renamed"), settings
        )
        assert state.active == "Snipe"
    finally:
        runtime.close()


def test_hide_and_unhide_preserve_cost_and_quality(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = Runtime(tmp_path, tmp_path / "plugins")
    item = item_ref(900, True)
    nq = item_ref(900, False)
    source = Source(
        SourceStatus(name=SourceName.CASHFLOW, recognised=True, found=True),
        Records(
            events=[
                event(SourceName.CASHFLOW, LedgerEventKind.BUY, item, 2, 1, 20),
                event(SourceName.CASHFLOW, LedgerEventKind.SELL, item, 1, 2, 15),
                event(SourceName.CASHFLOW, LedgerEventKind.BUY, nq, 1, 2, 3),
            ]
        ),
    )
    monkeypatch.setattr("gilhound.runtime.read_plugins", lambda _: [source])
    try:
        runtime.set_cost(item.id, item.hq, 9)
        original = runtime.ledger()
        runtime.set_personal(item.id, item.hq, True)
        assert runtime.hidden() == [item]
        assert runtime.ledger().positions == [p for p in original.positions if p.item == nq]
        assert runtime.ledger().summary.sales_proceeds == 0
        runtime.set_personal(item.id, item.hq, False)
        assert runtime.hidden() == []
        assert runtime.ledger() == original
    finally:
        runtime.close()


def test_cli_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert cli.main(["profiles"]) == 0
    assert {row["name"] for row in json.loads(capsys.readouterr().out)["rows"]} == {
        "Flip",
        "High margin",
        "Volume",
        "Snipe",
    }

    def scan(params: ScanParams, **kwargs: object) -> ScanResult:
        assert params.preset == "high_margin" and params.min_profit == 12000
        return ScanResult(params=params, rows=[], started_ts=1, finished_ts=2)

    monkeypatch.setattr("gilhound.runtime.run_scan", scan)
    assert cli.main(["scan", "--profile", "High margin"]) == 0


@pytest.mark.parametrize(("name", "phrases"), [
    ("Flip", ["5 sales", "positive", "5% buyer tax"]),
    ("High margin", ["4 distinct buyers", "12,000 gil"]),
    ("Volume", ["8 distinct buyers", "2.5 units/day", "6,000 gil"]),
    ("Snipe", ["50% of the Cerberus 7-day median", "5,000 gil", "1 to under 6 hours"]),
])
def test_profile_descriptions(
    name: str, phrases: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert cli.main(["profiles", "--describe", name]) == 0
    text = " ".join(json.loads(capsys.readouterr().out)["description"])
    assert all(phrase in text for phrase in phrases)
