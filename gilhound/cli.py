"""Compact standalone market and ledger commands."""

import argparse
import json
from collections.abc import Sequence
from typing import Any

from gilhound.models import RetainerScanParams, ScanPreset
from gilhound.runtime import Runtime


def rounded(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, dict):
        return {k: rounded(v) for k, v in value.items()}
    if isinstance(value, list):
        return [rounded(v) for v in value]
    return value


def project(row: dict, fields: list[str]) -> dict:
    if not fields:
        return row
    output = {}
    for field in fields:
        value = row
        for part in field.split("."):
            if not isinstance(value, dict) or part not in value:
                raise ValueError(f"Unknown field {field}")
            value = value[part]
        output[field] = value
    return output


def report(runtime: Runtime, args: argparse.Namespace) -> dict:
    fields = [s.strip() for s in args.fields.split(",") if s.strip()]
    result = {"command": args.command}
    if args.command == "scan":
        scan = runtime.scan(runtime.params(ScanPreset(args.preset.replace("-", "_"))))
        rows = scan.rows
        result.update(
            started_ts=scan.started_ts,
            finished_ts=scan.finished_ts,
            warning_count=len(scan.warnings),
        )
    elif args.command == "retainer":
        listing = runtime.retainer(RetainerScanParams(retainer_name=args.name, world=args.world))
        rows = listing.rows
        result.update(
            items_scanned=listing.items_scanned,
            duration_s=listing.duration_s,
            warning_count=len(listing.warnings),
        )
    elif args.command == "snipes":
        rows = runtime.run_snipes() if args.run else runtime.history.hits()
    elif args.command == "positions":
        rows = runtime.ledger().positions
    elif args.command == "sources":
        rows = runtime.sources()
    else:
        return {**result, **project(runtime.ledger().summary.model_dump(), fields)}
    return {
        **result,
        "total": len(rows),
        "rows": [project(r.model_dump(), fields) for r in rows[: args.top]],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gilhound")
    commands = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    style = common.add_mutually_exclusive_group()
    style.add_argument("--json", action="store_true")
    style.add_argument("--table", action="store_true")
    common.add_argument("--top", type=int, default=20)
    common.add_argument("--fields", default="")
    scan = commands.add_parser("scan", parents=[common])
    scan.add_argument("--preset", choices=[*ScanPreset, "high-margin"], default="flip")
    retainer = commands.add_parser("retainer", parents=[common])
    retainer.add_argument("name")
    retainer.add_argument("--world")
    commands.add_parser("snipes", parents=[common]).add_argument("--run", action="store_true")
    for name in ("positions", "pnl", "sources"):
        commands.add_parser(name, parents=[common])
    commands.add_parser("serve").add_argument("--port", type=int, default=8765)
    commands.add_parser("app")
    args = parser.parse_args(argv)
    if args.command in {"serve", "app"}:
        from gilhound import desktop

        if args.command == "app":
            return desktop.main()
        desktop.serve(args.port)
        return 0
    if args.top < 0:
        parser.error("--top must be nonnegative")
    runtime = Runtime()
    try:
        result = rounded(report(runtime, args))
    except Exception as exc:
        print(json.dumps({"command": args.command, "error": type(exc).__name__}))
        return 1
    finally:
        runtime.close()
    if args.table:
        rows = result.get("rows", [result])
        if rows:
            print("\t".join(rows[0]))
        for row in rows:
            print(
                "\t".join(
                    json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else str(v)
                    for v in row.values()
                )
            )
    else:
        print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
