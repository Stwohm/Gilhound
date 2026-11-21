from collections import Counter
from pathlib import Path

import pytest

from gilhound.market.retainer import run_retainer_scan
from gilhound.models import RetainerScanParams
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
