from collections import Counter
from pathlib import Path

import pytest

from gilhound.market.retainer import run_retainer_scan
from gilhound.models import RetainerScanParams
from gilhound.runtime import Runtime
from gilhound.sources.fixtures import install

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/market/retainer"


def test_retainer_replay() -> None:
    params = RetainerScanParams.model_validate_json((FIXTURE / "target.json").read_bytes())
    assert params.retainer_name.startswith("anon_")
    params.retainer_name = params.retainer_name.upper()
    steps = []
    with install("replay", FIXTURE) as session:
        result = run_retainer_scan(
            params, clock=session.now, progress=lambda done, total: steps.append((done, total))
        )
        assert session.used == session.index["requests"].keys()
    assert steps == [(0, 300), (100, 300), (200, 300), (300, 300)]
    assert result.items_scanned == 300
    assert not result.warnings
    assert Counter(r.item.id for r in result.rows) == {3: 4, 9: 12, 10: 2, 11: 1, 12: 1}
    for row in result.rows:
        assert row.cheapest_world and row.cheapest_dc in {"Chaos", "Light"}
        assert row.cheapest_qty > 0
        assert row.data_age_h >= 0
        assert row.gap_pct == pytest.approx(100 * (1 - row.cheapest_ppu / row.their_ppu))
        assert row.profit_per_unit == pytest.approx(row.their_ppu * 0.95 - row.cheapest_ppu * 1.05)

def test_retainer_trip_uses_cheapest_stack() -> None:
    params = RetainerScanParams.model_validate_json((FIXTURE / "target.json").read_bytes())
    costs = {"Light": 1200, "Chaos": 300}
    with install("replay", FIXTURE) as session:
        rows = run_retainer_scan(params, clock=session.now, travel_cost=costs).rows
    assert rows
    for row in rows:
        assert row.profit_qty == row.cheapest_qty
        gross = row.their_ppu * 0.95 - row.cheapest_ppu * 1.05
        assert row.profit_per_unit == pytest.approx(gross - costs[row.cheapest_dc] / row.profit_qty)


def test_runtime_retainer_applies_configured_trip_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    params = RetainerScanParams.model_validate_json((FIXTURE / "target.json").read_bytes())
    runtime = Runtime(tmp_path)
    monkeypatch.setattr(runtime.items, "decorate", lambda *args, **kwargs: None)
    trip_cost = 1200
    try:
        settings = runtime.settings.get()
        settings.travel_cost = {"Light": 0}
        runtime.save_settings(settings)
        with install("replay", FIXTURE):
            baseline = runtime.retainer(params).rows

        settings.travel_cost = {"Light": trip_cost}
        runtime.save_settings(settings)
        with install("replay", FIXTURE):
            charged = runtime.retainer(params).rows
    finally:
        runtime.close()

    assert any(row.cheapest_dc == "Light" for row in charged)
    for original, row in zip(baseline, charged, strict=True):
        assert original.profit_per_unit is not None
        assert row.profit_qty is not None
        cost_per_unit = trip_cost / row.profit_qty if row.cheapest_dc == "Light" else 0
        assert row.profit_per_unit == pytest.approx(
            original.profit_per_unit - cost_per_unit, abs=1e-6
        )
