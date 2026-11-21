"""Command-line interface for Gilhound."""

import argparse
import json
from collections.abc import Sequence

from gilhound.models import ScanPreset


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gilhound market and ledger tools.")
    commands = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="Emit compact JSON (the default).")

    scan = commands.add_parser("scan", parents=[common], help="Scan for market flips.")
    scan.add_argument("--preset", choices=list(ScanPreset), default=ScanPreset.FLIP)
    scan.add_argument("--top", type=int, default=20, help="Maximum number of rows to return.")
    scan.add_argument("--fields", help="Comma-separated output fields.")

    retainer = commands.add_parser("retainer", parents=[common], help="Scan a retainer's listings.")
    retainer.add_argument("name", help="Retainer name.")
    retainer.add_argument("--world", help="Search world (defaults to the configured home world).")

    commands.add_parser("snipes", parents=[common], help="Show recent snipe hits.")
    commands.add_parser("positions", parents=[common], help="Show open ledger positions.")

    args = parser.parse_args(argv)
    print(json.dumps({"command": args.command, "error": "not implemented"}, separators=(",", ":")))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
