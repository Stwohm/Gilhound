import json
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from gilhound import cli
from gilhound.market.http import HttpClient, RateLimiter
from gilhound.market.presets import run_scan
from gilhound.market.universalis import Universalis
from gilhound.models import Position, ScanPreset, ScanResult
from gilhound.runtime import Runtime
from gilhound.sources.fixtures import install


def test_startup_failure_returns_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    root = tmp_path / "Gilhound"
    root.mkdir()
    (root / "settings.json").write_text("malformed")
    assert cli.main(["sources"]) != 0
    assert json.loads(capsys.readouterr().out) == {"command": "sources", "error": "ValueError"}


@pytest.mark.parametrize("top", [0, 1])
def test_history_total_before_top(
    top: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    runtime = Runtime()
    now = int(time.time())
    try:
        for preset in (ScanPreset.FLIP, ScanPreset.SNIPE, ScanPreset.VOLUME):
            runtime.history.save_scan(ScanResult(
                params=runtime.params(preset), rows=[], started_ts=now, finished_ts=now
            ))
    finally:
        runtime.close()
    assert cli.main(["history", "--kind", "scan", "--top", str(top), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["total"] == 2
    assert len(result["rows"]) == top


@pytest.mark.parametrize("command", ["serve", "app"])
def test_desktop_startup_errors_return_json(
    command: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from gilhound import desktop

    def failed_server(port: int) -> None:
        raise SystemExit(3)

    monkeypatch.setattr(desktop, "serve", failed_server)
    monkeypatch.setattr(desktop, "main", lambda _: 1)
    assert cli.main([command]) != 0
    error = json.loads(capsys.readouterr().out)
    expected = "SystemExit" if command == "serve" else "RuntimeError"
    assert error == {"command": command, "error": expected}


@pytest.mark.parametrize(
    ("options", "length"), [([], 20), (["--top", "2"], 2), (["--top", "0"], 0)]
)
def test_json_rows(
    options: list[str],
    length: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = Runtime(tmp_path, tmp_path / "empty")
    ledger = runtime.ledger()
    ledger.positions = [
        Position.model_validate(
            dict(
                item=dict(id=i + 1, name="Item", hq=False),
                qty_open=2,
                avg_cost=1.23456,
                cost_known=True,
                listed_qty=0,
                realised_profit=0,
            )
        )
        for i in range(30)
    ]
    monkeypatch.setattr(runtime, "ledger", lambda: ledger)
    monkeypatch.setattr(cli, "Runtime", lambda: runtime)
    assert cli.main(["positions", "--fields", "item.id,avg_cost", "--json", *options]) == 0
    output = capsys.readouterr().out
    result = json.loads(output)
    assert len(output.splitlines()) == 1
    assert result == {
        "command": "positions",
        "total": 30,
        "rows": [{"item.id": i + 1, "avg_cost": 1.23} for i in range(length)],
    }


def test_scan_replay(
    extended_replay: Callable,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    market = Path(__file__).parent / "fixtures" / "market"
    http = HttpClient(limiter=RateLimiter(clock=lambda: 0, sleep=lambda _: None))
    with install("replay", market / "retainer"):
        Universalis(http).data_centers()
    monkeypatch.setenv("APPDATA", str(tmp_path))
    with install("replay", extended_replay(market / "hiscan_vol")) as replay:
        monkeypatch.setattr(
            "gilhound.runtime.run_scan",
            lambda params, **_: run_scan(params, clock=replay.now, http=http),
        )
        assert (
            cli.main(["scan", "--preset", "volume", "--json", "--fields", "item.id,buy_ppu"]) == 0
        )
        assert replay.used == replay.index["requests"].keys()
    raw = capsys.readouterr().out
    result = json.loads(raw)
    assert len(raw.splitlines()) == 1 and result["command"] == "scan"
    assert len(result["rows"]) == min(20, result["total"]) > 0
    assert all(row.keys() == {"item.id", "buy_ppu"} for row in result["rows"])
    # Keep archive reads at the replay timestamp, independent of today's retention cutoff.
    with install("replay", market / "hiscan_vol"):
        assert cli.main(["history", "--kind", "scan", "--top", "1", "--json"]) == 0
        (summary,) = json.loads(capsys.readouterr().out)["rows"]
        assert summary["profile"] == "Volume" and summary["row_count"] == result["total"]
        assert cli.main(["history", "show", str(summary["id"])]) == 0
        entry = json.loads(capsys.readouterr().out)
        assert entry["result"]["params"]["preset"] == "volume" and entry["description"]
        runtime = Runtime()
        try:
            item_name = entry["result"]["rows"][0]["item"]["name"]
            assert runtime.history.entries(search=item_name.lower())[0].id == summary["id"]
        finally:
            runtime.close()
