"""Record legacy market scans, anonymize them, and regenerate their replay goldens."""

import argparse
import contextlib
import io
import json
import runpy
import sys
import tempfile
from functools import partial
from pathlib import Path
from typing import Any, Literal
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gilhound.sources.fixtures import install
from tools.check_privacy import DEFAULT_PATTERNS

SCRIPTS = ("flip_scan", "hiscan2", "hiscan_vol", "snipe")
SKIPPED = ("restock", "bigcheck")
CANDIDATE_LIMITS = {"flip_scan": 8, "hiscan2": 8, "hiscan_vol": 24, "snipe": 24}
MARKET = ROOT / "tests/fixtures/market"
GOLDEN = ROOT / "tests/golden"


def capped_candidates(url: str, payload: Any, *, limit: int) -> Any:
    if url != "https://api.saddlebagexchange.com/api/scan":
        return payload
    if isinstance(payload, dict) and "data" not in payload:
        return payload
    rows = payload["data"] if isinstance(payload, dict) else payload
    ranked = sorted(rows, key=lambda row: (-row["profit_amount"], row["item_id"]))
    # Keep the historical response order for ties in the legacy ranking.
    selected = {id(row) for row in ranked[:limit]}
    capped = [row for row in rows if id(row) in selected]
    return {**payload, "data": capped} if isinstance(payload, dict) else capped


def run_scan(script: str, mode: Literal["record", "replay"], directory: Path) -> str:
    source = ROOT / "legacy" / f"{script}.py"
    stdout, stderr = io.StringIO(), io.StringIO()
    transform = (
        partial(capped_candidates, limit=CANDIDATE_LIMITS[script])
        if mode == "record"
        else None
    )
    with (
        patch.object(sys, "argv", [str(source)]),
        patch.object(sys, "path", [str(source.parent), *sys.path]),
        contextlib.redirect_stdout(stdout),
        contextlib.redirect_stderr(stderr),
        install(mode, directory, transform=transform) as session,
    ):
        runpy.run_path(str(source), run_name="__main__")
    if session.unanswered:
        raise RuntimeError(
            f"{script}: {len(session.unanswered)} HTTP requests went unanswered"
        )
    if not session.used or len(session.used) != len(session.index["requests"]):
        raise RuntimeError(f"{script}: replay did not exercise every recording")
    if "sb err" in stderr.getvalue():
        raise RuntimeError(f"{script}: candidate scan raised an error")
    return stdout.getvalue()


def main() -> None:
    from tools.anonymize import Anonymizer, anonymize_recording, get_salt
    from tools.check_privacy import load_patterns, scan

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patterns", type=Path, default=DEFAULT_PATTERNS)
    parser.add_argument("scripts", nargs="*", metavar="SCRIPT")
    args = parser.parse_args()
    if set(args.scripts) - {*SCRIPTS, *SKIPPED}:
        parser.error(f"Scripts must be selected from {', '.join((*SCRIPTS, *SKIPPED))}")
    scripts = args.scripts or SCRIPTS
    if all(script in SKIPPED for script in scripts):
        for script in scripts:
            print(f"{script}: skipped (personal input embedded in legacy script)")
        return
    anonymizer = Anonymizer(get_salt(), load_patterns(args.patterns))
    with tempfile.TemporaryDirectory(prefix="gilhound-market-") as temp:
        stage = Path(temp).resolve()
        if stage.is_relative_to(ROOT):
            raise ValueError("Temporary recordings must be outside the repository")
        for script in scripts:
            if script in SKIPPED:
                print(
                    f"{script}: skipped (personal input embedded in legacy script)",
                    flush=True,
                )
                continue
            print(f"{script}: recording", flush=True)
            run_scan(script, "record", stage / "raw")
            clean = stage / "clean"
            anonymize_recording(stage / "raw", clean, anonymizer)
            output = run_scan(script, "replay", clean)
            golden = stage / f"{script}.txt"
            golden.write_text(output, encoding="utf-8", newline="\n")
            if scan([clean, golden], anonymizer.patterns)[0]:
                raise RuntimeError("Staged fixtures failed the privacy scan")
            previous = [
                path for path in MARKET.glob("*/*.json") if path.parent.name != script
            ]
            size = sum(
                path.stat().st_size for path in (*previous, *clean.glob("*.json"))
            )
            if size >= 15_000_000:
                raise RuntimeError("Market fixture size would exceed 15 MB")
            destination = MARKET / script
            destination.mkdir(parents=True, exist_ok=True)
            for path in clean.glob("*.json"):
                (destination / path.name).write_bytes(path.read_bytes())
            keys = json.loads((clean / "index.json").read_text(encoding="utf-8"))[
                "requests"
            ]
            for path in destination.glob("*.json"):
                if path.name != "index.json" and path.stem not in keys:
                    path.unlink()
            GOLDEN.mkdir(parents=True, exist_ok=True)
            (GOLDEN / golden.name).write_bytes(golden.read_bytes())
            for folder in (stage / "raw", clean):
                for path in folder.iterdir():
                    path.unlink()
                folder.rmdir()
            print(
                f"{script}: {len(keys)} requests, {len(output.splitlines()) - 1} result rows"
            )


if __name__ == "__main__":
    main()
