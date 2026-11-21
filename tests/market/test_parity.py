from collections.abc import Callable
from pathlib import Path

import pytest

from gilhound.market.http import HttpClient, RateLimiter
from gilhound.market.presets import run_scan
from gilhound.market.snipe import evaluate
from gilhound.market.universalis import Universalis
from gilhound.models import FreshnessTier, ScanParams, ScanPreset, ScanResult, SnipeRule
from gilhound.sources.fixtures import install

TESTS = Path(__file__).resolve().parents[1]
MARKET = TESTS / "fixtures/market"
CASES = [
    (ScanPreset.FLIP, "flip_scan"),
    (ScanPreset.HIGH_MARGIN, "hiscan2"),
    (ScanPreset.VOLUME, "hiscan_vol"),
    (ScanPreset.SNIPE, "snipe"),
]

# Legacy compatibility keeps the full-stack travel allocation and wait, spread-based
# rank and first-listing snipe benchmark. Live scans cap the trade and evaluate
# listing freshness before the hit limit. The historical goldens are unchanged.
# The legacy quantity column includes four Twintania units alongside the
# single Phoenix unit. Keep legacy ranking but report the actual world stock.
KNOWN_DIFFS = {("Scroll Cabinet", "Phoenix"): (5, 1)}


def replay(
    preset: ScanPreset, script: str, *, compat: bool, extended: Callable = lambda p: p
) -> ScanResult:
    http = HttpClient(limiter=RateLimiter(clock=lambda: 0, sleep=lambda _: None))
    if not compat:
        # Prime the same client's metadata cache from its separate recording.
        with install("replay", MARKET / "retainer"):
            Universalis(http).data_centers()
    params = ScanParams(
        preset=preset,
        home_world="Cerberus",
        buy_region="Europe",
        seller_tax=0.05,
        **({"buyer_tax": 0} if compat else {}),
    )
    with install("replay", extended(MARKET / script)) as session:
        result = run_scan(params, clock=session.now, legacy_compat=compat, http=http)
        assert session.used == session.index["requests"].keys()
        assert not session.unanswered
        assert not result.warnings
        return result


@pytest.mark.parametrize(("preset", "script"), CASES)
def test_legacy_parity(preset: ScanPreset, script: str) -> None:
    expected = []
    for line in (TESTS / f"golden/{script}.txt").read_text(encoding="utf-8").splitlines()[1:]:
        cells = [s.strip() for s in line.split("|")]
        expected.append(
            (
                cells[0],
                cells[1],
                cells[2].split(" (")[0],
                int(cells[4 if preset == ScanPreset.SNIPE else 3]),
                int(cells[-1]),
            )
        )
    rows = replay(preset, script, compat=True).rows
    assert [
        (r.item.name, "HQ" if r.item.hq else "NQ", r.buy_world, r.buy_ppu, r.profit_per_unit)
        for r in rows
    ] == expected


def test_default_both_taxes(extended_replay: Callable) -> None:
    result = replay(ScanPreset.FLIP, "flip_scan", compat=False, extended=extended_replay)
    assert result.rows
    for row in result.rows:
        assert row.profit_per_unit == pytest.approx(row.home_median_7d * 0.95 - row.buy_ppu * 1.05)
        assert row.total_profit_capped == pytest.approx(row.profit_per_unit * row.sellable_qty)
    for (item, world), (_, correct) in KNOWN_DIFFS.items():
        (row,) = [r for r in result.rows if (r.item.name, r.buy_world) == (item, world)]
        assert row.buy_qty_at_price == correct


def test_live_snipe_candidates_reach_rule_evaluation(extended_replay: Callable) -> None:
    result = replay(ScanPreset.SNIPE, "snipe", compat=False, extended=extended_replay)
    hits = evaluate(
        result.rows,
        SnipeRule(max_top_buyer_share=1),
        [FreshnessTier(name="all ages", max_age_h=1000, send_discord=False)],
        result.finished_ts,
    )
    assert hits
    assert all(hit.buy_ppu <= hit.home_median_7d / 2 for hit in hits)
