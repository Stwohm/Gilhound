"""Anonymize recorded market JSON and the supported plugin exports."""

import argparse
import csv
import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gilhound.sources.fixtures import write_json
from tools.check_privacy import DEFAULT_PATTERNS, load_patterns, scan

MARKET_IDENTITIES = {
    "buyername",
    "retainername",
    "sellerid",
    "creatorname",
    "creatorid",
    "listingid",
    "retainerid",
    "characterid",
    "charactername",
    "sellername",
    "buyerid",
    "playername",
    "playerid",
}
AMOUNTS = {
    "price",
    "priceperunit",
    "unitprice",
    "total",
    "tax",
    "gil",
    "cost",
    "profit",
    "gilplayer",
    "gilretainer",
    "prices",
    "firstprice",
    "lastprice",
    "lowestprice",
    "oldprice",
    "lowestcompetitor",
    "newprice",
    "soldtotalgil",
    "soldgrossgil",
    "grossgil",
    "netgil",
    "totaltax",
    "taxpaid",
}


def field_name(field: str) -> str:
    return field.replace("_", "").casefold()


def get_salt(value: str | None = None) -> bytes:
    salt = value or os.environ.get("GILHOUND_FIXTURE_SALT")
    if not salt:
        raise SystemExit("Supply a salt via GILHOUND_FIXTURE_SALT or --salt")
    return salt.encode()


class Anonymizer:
    def __init__(self, salt: bytes, patterns: list[re.Pattern[str]]) -> None:
        self.salt = salt
        self.patterns = patterns

    def digest(self, value: Any, namespace: str = "identity") -> str:
        message = f"{namespace}\0{str(value).casefold()}".encode()
        return hmac.new(self.salt, message, hashlib.sha256).hexdigest()

    def identity(self, value: Any, *, numeric: bool = False) -> Any:
        if value is None or value == "":
            return value
        if numeric and str(value).isdigit():
            result = 1 + int(self.digest(value, "numeric")[:15], 16)
            return str(result) if isinstance(value, str) else result
        return "anon_" + self.digest(value)[:24]

    def matches(self, value: str) -> bool:
        return any(pattern.search(value) for pattern in self.patterns)

    def market(self, value: Any, field: str = "", scale: Decimal = Decimal(1)) -> Any:
        key = field_name(field)
        if isinstance(value, dict):
            return {
                self.identity(k) if self.matches(k) else k: self.market(v, k, scale)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [self.market(v, field, scale) for v in value]
        if key in MARKET_IDENTITIES or (isinstance(value, str) and self.matches(value)):
            return self.identity(value)
        if key in AMOUNTS:
            return scaled(value, scale)
        return value


def scaled(value: Any, scale: Decimal) -> Any:
    if value is None or value == "" or scale == 1:
        return value
    if isinstance(value, bool):
        raise TypeError("Unexpected boolean amount")
    result = round(Decimal(str(value)) * scale)
    return str(result) if isinstance(value, str) else result


def anonymize_recording(
    source: Path, destination: Path, anonymizer: Anonymizer, scale: Decimal = Decimal(1)
) -> None:
    index = json.loads((source / "index.json").read_text(encoding="utf-8"))
    if any(
        anonymizer.matches(request["url"]) for request in index["requests"].values()
    ):
        raise ValueError("Recording contains a private request URL")
    destination.mkdir(parents=True, exist_ok=True)
    for key in index["requests"]:
        if not re.fullmatch("[0-9a-f]{40}", key):
            raise ValueError("Invalid request digest")
        path = source / f"{key}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        write_json(destination / path.name, anonymizer.market(payload, scale=scale))
    write_json(destination / "index.json", index)


CASHFLOW = {
    "RetainerSales": "Cid RetainerName Item Price Quantity IsMannequinn UnixTime Buyer",
    "GilRecords": "Cid GilPlayer GilRetainer UnixTime",
    "ShopPurchases": "Cid RetainerName Item Price Quantity IsMannequinn UnixTime",
}
RESTOCKY_CSV = {
    "listings.csv": (
        "listing_id retainer item_id item_name hq qty first_seen_utc first_price last_price "
        "reprice_count undercut_count undercutters competitors ended_utc outcome sold_total_gil "
        "days_listed buyer sale_time_utc sold_gross_gil is_mannequin cost profit"
    ),
    "competitors.csv": (
        "item_id item_name hq competitor times_seen times_below_us lowest_price last_qty max_qty "
        "first_seen_utc last_seen_utc listings_contested"
    ),
    "price-history.csv": (
        "utc_time retainer slot item_id hq qty old_price lowest_competitor new_price applied "
        "reason undercutters"
    ),
}
PLUGIN_IDENTITIES = MARKET_IDENTITIES | {
    "buyer",
    "retainer",
    "retainerkey",
    "cid",
    "seller",
    "competitor",
    "historykey",
}
PLUGIN_AMOUNTS = AMOUNTS | {"consumedcost", "unitcost"}
RESTOCKY_FIELDS = {
    "id",
    "retainerkey",
    "retainername",
    "itemid",
    "itemname",
    "ishq",
    "quantity",
    "slot",
    "firstseenutc",
    "firstprice",
    "lastprice",
    "lastseenutc",
    "repricecount",
    "undercutcount",
    "competitors",
    "missingsinceutc",
    "endedutc",
    "outcome",
    "soldtotalgil",
    "soldrecordedutc",
    "soldsource",
    "historykey",
    "buyername",
    "ismannequin",
    "soldgrossgil",
    "saletimeutc",
    "historyonly",
    "consumedcost",
    "unknowncostunits",
    "stucknotified",
    "dayslisted",
    "seller",
    "lowestprice",
    "timesseen",
    "timesbelowus",
    "lastquantity",
    "maxquantity",
    "utc",
    "remainingquantity",
    "unitcost",
    "source",
}
ALLAGAN_DATE = "%m/%d/%Y %H:%M:%S"


class PluginAnonymizer:
    def __init__(self, anonymizer: Anonymizer, scale: Decimal) -> None:
        self.anonymizer = anonymizer
        self.scale = scale
        self.offset = timedelta(days=90 + int(anonymizer.digest("time")[:8], 16) % 900)

    def numeric_identity(self, value: int | str) -> Any:
        return self.anonymizer.identity(value, numeric=True) if int(value) else value

    def date(self, value: str, fmt: str | None = None) -> str:
        dt = datetime.strptime(value, fmt) if fmt else datetime.fromisoformat(value)
        shifted = dt + self.offset
        if fmt:
            return shifted.strftime(fmt)
        result = shifted.isoformat(
            timespec="microseconds" if dt.microsecond else "seconds"
        )
        return result.removesuffix("+00:00") + "Z" if value.endswith("Z") else result

    def value(self, field: str, value: Any) -> Any:
        if value is None or value == "":
            return value
        key = field_name(field)
        if key == "cid":
            return self.numeric_identity(value)
        if key == "unixtime":
            # The CashFlow export uses milliseconds. Keep the source schema and unit.
            return int(value) + self.offset.days * 86_400_000
        if key in {"id", "listingid"}:
            return str(UUID(hex=self.anonymizer.digest(value)[:32]))
        if key in {"competitors", "undercutters"} and isinstance(value, str):
            entries = []
            for entry in value.split(";"):
                match = re.fullmatch(r"(.+?)( \d+x)", entry)
                name, suffix = match.groups() if match else (entry, "")
                entries.append(self.anonymizer.identity(name) + suffix)
            return ";".join(entries)
        if key in PLUGIN_IDENTITIES:
            return self.anonymizer.identity(value)
        if key in PLUGIN_AMOUNTS:
            return scaled(value, self.scale)
        if key.endswith("utc") or key in {"utc", "utctime"}:
            return self.date(value)
        if isinstance(value, str) and self.anonymizer.matches(value):
            return self.anonymizer.identity(value)
        return value

    def restocky(self, value: Any) -> Any:
        if isinstance(value, list):
            return [self.restocky(record) for record in value]
        if not isinstance(value, dict):
            raise TypeError("Unknown Restocky JSON shape")
        result: dict[str, Any] = {}
        for field, item in value.items():
            key = field_name(field)
            if key not in RESTOCKY_FIELDS:
                raise ValueError("Unknown Restocky JSON field")
            if key == "competitors":
                result[field] = {
                    self.anonymizer.identity(name): self.restocky(record)
                    for name, record in item.items()
                }
            else:
                if isinstance(item, (list, dict)):
                    raise ValueError("Unknown nested Restocky JSON field")
                result[field] = self.value(field, item)
        return result

    def cashflow(self, source: Path, target: Path) -> None:
        with (
            closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as live,
            closing(sqlite3.connect(":memory:")) as backup,
            closing(sqlite3.connect(target)) as clean,
        ):
            live.backup(backup)
            for table, fields in CASHFLOW.items():
                columns = fields.split()
                metadata = list(backup.execute(f"PRAGMA table_info({table})"))
                types = {row[1]: row[2] for row in metadata}
                if set(columns) != types.keys() or set(types.values()) - {
                    "TEXT",
                    "INTEGER",
                    "NUMERIC",
                }:
                    raise ValueError(f"Unknown CashFlow schema for {table}")
                schema = ", ".join(f"{column} {types[column]}" for column in columns)
                clean.execute(f"CREATE TABLE {table} ({schema})")
                data = backup.execute(f"SELECT {', '.join(columns)} FROM {table}")
                clean.executemany(
                    f"INSERT INTO {table} VALUES ({', '.join('?' for _ in columns)})",
                    (
                        tuple(
                            self.value(k, v) for k, v in zip(columns, row, strict=True)
                        )
                        for row in data
                    ),
                )
            clean.commit()

    def csv(self, source: Path, target: Path) -> None:
        with (
            source.open(encoding="utf-8-sig", newline="") as raw,
            target.open("w", encoding="utf-8", newline="") as clean,
        ):
            reader, writer = csv.reader(raw), csv.writer(clean, lineterminator="\n")
            if source.parent.name == "Restocky":
                header = next(reader, [])
                if header != RESTOCKY_CSV[source.name].split():
                    raise ValueError("Unknown Restocky CSV header")
                writer.writerow(header)
                for row in reader:
                    writer.writerow(
                        self.value(k, v) for k, v in zip(header, row, strict=True)
                    )
            else:
                for row in reader:
                    writer.writerow(self.positional_row(source.name, row))

    def positional_row(self, name: str, row: list[str]) -> list[Any]:
        if name == "inventories.csv" and len(row) == 27:
            for cell in row[:25]:
                int(cell)
            if any(not key.isdigit() for key in row[25].split(";") if key):
                raise ValueError("Unknown InventoryTools gear set ID")
            quantity = scaled(row[3], self.scale) if row[2] == "1" else row[3]
            return [
                *row[:3],
                quantity,
                *row[4:23],
                self.numeric_identity(row[23]),
                scaled(row[24], self.scale),
                row[25],
                ";".join(self.anonymizer.identity(name) for name in row[26].split(";")),
            ]
        if (name, len(row)) in {("SaleItems.csv", 10), ("SoldItems.csv", 8)}:
            for cell in row[:6]:
                int(cell)
            result = [
                self.numeric_identity(row[0]),
                *row[1:5],
                scaled(row[5], self.scale),
            ]
            if name == "SoldItems.csv":
                int(row[6])
                return [*result, row[6], self.date(row[7], ALLAGAN_DATE)]
            int(row[9])
            return [
                *result,
                "",
                self.date(row[7], ALLAGAN_DATE),
                self.date(row[8], ALLAGAN_DATE),
                row[9],
            ]
        raise ValueError("Unknown plugin CSV schema")


def anonymize_plugins(
    source: Path, destination: Path, cleaner: PluginAnonymizer
) -> None:
    exports = (
        "CashFlow/DefaultDbV2.sqlite",
        "AllaganMarket/SaleItems.csv",
        "AllaganMarket/SoldItems.csv",
        "InventoryTools/inventories.csv",
        *(f"Restocky/{name}" for name in RESTOCKY_CSV),
        "Restocky/listings.json",
        "Restocky/cost-ledger.json",
    )
    if destination.resolve().is_relative_to(source.resolve()):
        raise ValueError(
            "Plugin fixture output must be outside the live plugin directory"
        )
    with tempfile.TemporaryDirectory(prefix="gilhound-plugins-") as temp:
        staged = Path(temp)
        for name in exports:
            raw, clean = source / name, staged / name
            if not raw.is_file():
                if name in {"Restocky/listings.json", "Restocky/listings.csv"}:
                    continue
                raise ValueError(f"Missing required plugin export {name}")
            clean.parent.mkdir(parents=True, exist_ok=True)
            if raw.suffix == ".sqlite":
                # Use a git-trackable extension. The contents are a fresh SQLite database.
                cleaner.cashflow(raw, clean.with_suffix(".db"))
            elif raw.suffix == ".csv":
                cleaner.csv(raw, clean)
            else:
                write_json(
                    clean,
                    cleaner.restocky(json.loads(raw.read_text(encoding="utf-8-sig"))),
                )
        if not any((staged / "Restocky").glob("listings.*")):
            raise ValueError("Missing Restocky listings exports")
        if scan([staged], cleaner.anonymizer.patterns)[0]:
            raise ValueError("Staged plugins failed the privacy scan")
        shutil.copytree(staged, destination, dirs_exist_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patterns", type=Path, default=DEFAULT_PATTERNS)
    parser.add_argument("--salt")
    parser.add_argument(
        "--scale",
        type=Decimal,
        help="Amount multiplier, also read from GILHOUND_FIXTURE_SCALE",
    )
    commands = parser.add_subparsers(dest="mode", required=True)
    market = commands.add_parser("market")
    market.add_argument("source", type=Path)
    market.add_argument("destination", type=Path)
    plugins = commands.add_parser("plugins")
    plugins.add_argument("--source", type=Path)
    plugins.add_argument(
        "--destination", type=Path, default=ROOT / "tests/fixtures/plugins"
    )
    args = parser.parse_args()
    anonymizer = Anonymizer(get_salt(args.salt), load_patterns(args.patterns))
    scale = args.scale
    if scale is None and os.environ.get("GILHOUND_FIXTURE_SCALE"):
        scale = Decimal(os.environ["GILHOUND_FIXTURE_SCALE"])
    if scale is None:
        scale = (
            Decimal(1)
            if args.mode == "market"
            else (
                Decimal(2)
                + Decimal(int(anonymizer.digest("scale")[:8], 16)) / Decimal(2**32)
            )
        )
    if not scale.is_finite() or scale <= 0 or (args.mode == "plugins" and scale == 1):
        parser.error("Scale must be positive and finite, and differ from 1 for plugins")
    try:
        if args.mode == "market":
            if args.destination.resolve().is_relative_to(args.source.resolve()):
                raise ValueError("Anonymized recordings require a separate destination")
            with tempfile.TemporaryDirectory(prefix="gilhound-clean-") as temp:
                stage = Path(temp)
                anonymize_recording(args.source, stage, anonymizer, scale)
                if scan([stage], anonymizer.patterns)[0]:
                    raise ValueError("Anonymized recording failed the privacy scan")
                shutil.copytree(stage, args.destination, dirs_exist_ok=True)
        else:
            source = (
                args.source or Path(os.environ["APPDATA"]) / "XIVLauncher/pluginConfigs"
            )
            anonymize_plugins(
                source.resolve(), args.destination, PluginAnonymizer(anonymizer, scale)
            )
    except (OSError, ValueError, TypeError, sqlite3.Error, ArithmeticError) as exc:
        # A parser exception can embed a field value. Keep it out of the terminal and logs.
        raise SystemExit(f"Anonymization stopped ({type(exc).__name__})") from None
    print(f"Anonymized {args.mode} fixtures written.")


if __name__ == "__main__":
    main()
