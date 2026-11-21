"""Capture a bounded, anonymised Universalis retainer scan for offline replay."""

import argparse
import secrets
import shutil
import sys
import tempfile
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gilhound.market.http import HttpClient
from gilhound.market.retainer import batches, run_retainer_scan
from gilhound.market.universalis import Universalis
from gilhound.models import RetainerScanParams
from gilhound.sources.fixtures import install, write_json
from tools.anonymize import Anonymizer
from tools.check_privacy import DEFAULT_PATTERNS, load_patterns, scan


def record(destination: Path, limit: int, world: str, region: str) -> None:
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Choose an empty destination")
    cleaner = Anonymizer(secrets.token_bytes(32), load_patterns(DEFAULT_PATTERNS))
    scale = Decimal(2) + Decimal(secrets.randbelow(10000)) / 10000

    def transform(url: str, raw: Any) -> Any:
        if url.endswith("/marketable"):
            raw = sorted(raw)[:limit]
        return cleaner.market(raw, scale=scale)

    with tempfile.TemporaryDirectory() as temp:
        stage = Path(temp)
        http = HttpClient()
        api = Universalis(http)
        with install("record", stage, transform=transform) as session:
            counts: Counter[str] = Counter()
            for _, snapshots in batches(api, world, api.marketable(), {}):
                counts.update(
                    row.retainer_name
                    for snapshot in snapshots.values()
                    for row in snapshot.listings
                    if row.retainer_name
                )
            name = counts.most_common(1)[0][0]
            params = RetainerScanParams(retainer_name=name, world=world, buy_region=region)
            result = run_retainer_scan(params, clock=session.now, http=http)
            if not result.rows or not any(row.cheapest_world for row in result.rows):
                raise ValueError("Capture yielded no regional comparisons")
        write_json(stage / "target.json", params.model_dump())
        if scan([stage], cleaner.patterns)[0]:
            raise ValueError("Staged recording failed privacy scan")
        shutil.copytree(stage, destination, dirs_exist_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=300, choices=range(1, 301), metavar="1..300")
    parser.add_argument("--world", default="Cerberus")
    parser.add_argument("--region", default="Europe")
    parser.add_argument("--destination", type=Path, default=ROOT / "tests/fixtures/market/retainer")
    args = parser.parse_args()
    record(args.destination, args.limit, args.world, args.region)
