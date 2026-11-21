"""Compact standalone market and ledger commands."""

import argparse
import json
import sys
import time
from collections.abc import Sequence
from contextlib import closing
from typing import Any

from gilhound.ledger.fifo import rollup_sales
from gilhound.models import PartnerShare, RetainerScanParams, ScanPreset
from gilhound.runtime import Runtime
from gilhound.runtime.partner import decode, encode
from gilhound.runtime.profiles import BUILTINS


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
    if fields == ["proof"] and args.command in {"scan", "snipes"}:
        fields = ["item.id", "item.name", "item.hq", "buy_world", "buy_ppu", "sellable_qty",
                  "profit_per_unit", "volume_proof.units_per_day",
                  "volume_proof.distinct_buyers_7d",
                  "volume_proof.last_sale_age_h", "volume_proof.top_buyer_share", "data_age_h"]
    result = {"command": args.command}
    total = None
    if args.command == "partner":
        settings = runtime.settings.get()
        if args.action == "share":
            share = PartnerShare(version=1, sender=settings.share_sender,
                                 created_ts=int(time.time()), items=settings.own_avoid,
                                 retainers=settings.own_retainers if args.include_retainers else [])
            return {"code": encode(share)}
        if args.action == "import":
            share = decode(args.code)
            previous = settings.partner_manual
            before = {(i.id, i.hq) for i in previous.items} if previous else set()
            after = {(i.id, i.hq) for i in share.items}
            changes = {"adds": sorted(after - before), "removes": sorted(before - after),
                       "created_ts": share.created_ts}
            if args.preview:
                return changes
            runtime.settings.save(settings.model_copy(update={
                "partner_manual": share.model_copy(update={"retainers": []}),
            }))
            return {**changes, "imported": len(share.items)}
        if args.action == "sweep":
            task = runtime.partners.refresh(force=True)
            if task:
                task.result()
        return runtime.partners.status(settings)
    if args.command == "scan":
        if args.profile:
            profiles = runtime.profiles.list("scan", runtime.settings.get())
            params = profiles.profiles[args.profile].params
        else:
            params = runtime.params(ScanPreset(args.preset.replace("-", "_")))
        profile = args.profile or next(
            name for name, preset in BUILTINS.items() if preset == params.preset
        )
        scan = runtime.scan(params, profile)
        if not fields:
            fields = ["item.name", "item.hq", "buy_world", "buy_ppu", "sellable_qty",
                      "profit_per_unit", "total_profit_capped", "partner"]
        rows = scan.rows
        result.update(
            started_ts=scan.started_ts,
            finished_ts=scan.finished_ts,
            warning_count=len(scan.warnings),
        )
    elif args.command == "history":
        if args.action == "show":
            return runtime.archive_entry(args.id).model_dump()
        rows, total = runtime.history.entry_page(kind=args.kind, limit=args.top)
    elif args.command == "profiles":
        profiles = runtime.profiles.list(args.library, runtime.settings.get())
        if args.describe:
            description = profiles.descriptions[args.describe]
            return {**result, "name": args.describe, "description": description}
        return {**result, "total": len(profiles.profiles), "rows": [
            {"name": name, "strategy": profile.params.preset}
            for name, profile in profiles.profiles.items()]}
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
        runtime.partners.mark(rows, runtime.settings.get())
    elif args.command == "sales":
        rows = runtime.ledger().filtered_sales(args.since, args.until, args.item, args.retainer)
        if args.rollup:
            rows = rollup_sales(rows)
    elif args.command == "positions":
        ledger = runtime.ledger()
        for warning in ledger.warnings:
            print(warning, file=sys.stderr)
        rows = [p for p in ledger.positions if (p.qty_open == 0 if args.closed else
                p.qty_open > 0 if args.unlisted else p.listed_qty > 0)]
    elif args.command == "sources":
        rows = runtime.sources()
    else:
        return {**result, **project(runtime.ledger().summary.model_dump(), fields)}
    visible = rows[: args.top]
    if args.command in {"snipes", "positions", "sales"} and (
        not fields
        or any(
            f in {"item", "item.name", "item.universalis_url", "item.saddlebag_url"} for f in fields
        )
    ):
        runtime.items.decorate(
            (r.item for r in visible), runtime.settings.get().home_world, wait=True
        )
    return {
        **result,
        "total": len(rows) if total is None else total,
        "rows": [project(r.model_dump(), fields) for r in visible],
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
    strategy = scan.add_mutually_exclusive_group()
    strategy.add_argument("--profile")
    profiles = commands.add_parser("profiles", parents=[common])
    profiles.add_argument("--describe", metavar="NAME")
    profiles.add_argument("--library", choices=["scan", "snipes"], default="scan")
    strategy.add_argument("--preset", choices=[*ScanPreset, "high-margin"], default="flip")
    history = commands.add_parser("history", parents=[common])
    history.add_argument("--kind", choices=["scan", "snipe", "retainer"])
    show = history.add_subparsers(dest="action").add_parser("show")
    show.add_argument("id", type=int)
    show.add_argument("--json", action="store_true")
    partner = commands.add_parser("partner", parents=[common])
    actions = partner.add_subparsers(dest="action", required=True)
    actions.add_parser("status")
    actions.add_parser("sweep")
    actions.add_parser("share").add_argument("--include-retainers", action="store_true")
    incoming = actions.add_parser("import")
    incoming.add_argument("code")
    incoming.add_argument("--preview", action="store_true", help="Show changes before importing")
    retainer = commands.add_parser("retainer", parents=[common])
    retainer.add_argument("name")
    retainer.add_argument("--world")
    commands.add_parser("snipes", parents=[common]).add_argument("--run", action="store_true")
    positions = commands.add_parser("positions", parents=[common])
    positions.add_argument("--closed", action="store_true")
    positions.add_argument("--unlisted", action="store_true", help="Include unlisted stock")
    sales = commands.add_parser("sales", parents=[common])
    sales.add_argument("--since", type=int, help="Inclusive UTC epoch seconds")
    sales.add_argument("--until", type=int, help="Exclusive UTC epoch seconds")
    sales.add_argument("--item", type=int)
    sales.add_argument("--retainer")
    sales.add_argument("--rollup", action="store_true", help="Group by item and quality")
    for name in ("pnl", "sources"):
        commands.add_parser(name, parents=[common])
    commands.add_parser("serve").add_argument("--port", type=int, default=8765)
    commands.add_parser("app")
    args = parser.parse_args(argv)
    if getattr(args, "top", 0) < 0:
        parser.error("--top must be nonnegative")
    try:
        if args.command in {"serve", "app"}:
            from gilhound import desktop

            if args.command == "serve":
                desktop.serve(args.port)
            elif desktop.main([]):
                raise RuntimeError("Desktop startup failed")
            return 0
        with closing(Runtime()) as runtime:
            result = report(runtime, args)
        if args.command != "history" or args.action != "show":
            result = rounded(result)
    except (Exception, SystemExit) as exc:
        if isinstance(exc, SystemExit) and exc.code in (None, 0):
            return 0
        print(json.dumps({"command": args.command, "error": type(exc).__name__}))
        return 1
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
