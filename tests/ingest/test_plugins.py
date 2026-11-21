import os
import sqlite3
from contextlib import closing
from datetime import timedelta, timezone
from pathlib import Path

import pytest

from gilhound.ingest import allagan_market, cashflow, discover, read_plugins, restocky
from gilhound.models import LedgerEventKind, SourceName

ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "plugins"
TZ = timezone(timedelta(hours=3))


def test_fixture_readers() -> None:
    cash, market, tools, rest = read_plugins(ROOT, local_tz=TZ)
    assert all(s.status.recognised for s in (cash, market, tools, rest))
    assert len(cash.records.balances) == 4
    assert len(cash.records.events) == 201
    assert len(market.records.events) == 75
    assert len(market.records.listings) == len(tools.records.listings) == 36
    assert all(s.container == 12002 and s.owner and s.ppu for s in tools.records.listings)
    assert any(s.item.hq for s in tools.records.inventory)
    assert len(rest.records.events) == 42
    assert len(rest.records.listings) == 36
    assert rest.records.remaining_costs and rest.records.competitors and rest.records.price_history
    assert isinstance(rest.records.price_history[0]["utc_time"], int)


def test_restocky_csv_mirror(tmp_path: Path) -> None:
    path = ROOT / "Restocky" / "listings.csv"
    (tmp_path / path.name).write_bytes(path.read_bytes())
    csv_only = restocky.read(tmp_path)
    full = restocky.read(path.parent)
    assert csv_only.status.recognised
    assert sorted(e.model_dump_json() for e in csv_only.records.events) == sorted(
        e.model_dump_json() for e in full.records.events
    )
    assert sorted(map(repr, csv_only.records.listings)) == sorted(map(repr, full.records.listings))


@pytest.mark.parametrize(
    ("folder", "filename", "bad", "label"),
    [
        ("AllaganMarket", "SoldItems.csv", "1,1,17,2,1,10,0,11/01/2020 00:00:00", "AllaganMarket"),
        ("InventoryTools", "inventories.csv", "0,0,1", "Allagan Tools"),
        ("Restocky", "cost-ledger.json", "[{}]", "Restocky"),
        ("Restocky", "listings.json", "[42]", "Restocky"),
        ("CashFlow", "DefaultDbV2.sqlite", "wrong", "CashFlow"),
    ],
)
def test_mismatch_and_missing(
    tmp_path: Path, folder: str, filename: str, bad: str, label: str
) -> None:
    (tmp_path / folder).mkdir()
    (tmp_path / folder / filename).write_text(bad, encoding="utf-8")
    result = discover(tmp_path)
    (found,) = [status for status in result if status.found]
    assert found.warning == f"{label} format not recognised"
    assert not found.recognised
    assert all(status.last_update_ts is None for status in result if not status.found)


def test_local_time_and_mtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "XIVLauncher" / "pluginConfigs" / "AllaganMarket"
    folder.mkdir(parents=True)
    path = folder / "SoldItems.csv"
    path.write_text("2,3,4,1,7,10,0,01/02/1970 03:00:00", encoding="utf-8")
    os.utime(path, (1234567, 1234567))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    (status,) = [s for s in discover() if s.name == SourceName.ALLAGAN_MARKET]
    assert status.last_update_ts == 1234567
    (row,) = allagan_market.read(folder, TZ).records.events
    assert (row.ts, row.item.hq, row.qty, row.stack_gil) == (86400, True, 7, 67)


def test_cashflow_wal_optional_tables_and_trades(tmp_path: Path) -> None:
    path = tmp_path / "DefaultDbV2.sqlite"
    original = ROOT / "CashFlow" / "DefaultDbV2.db"
    path.write_bytes(original.read_bytes())
    with closing(sqlite3.connect(path)) as live:
        live.execute("PRAGMA journal_mode=WAL")
        live.executescript("""
            CREATE TABLE Trades(Cid NUMERIC, Self NUMERIC, UnixTime, Gil INTEGER, Items BLOB);
            CREATE TABLE CidMap(Cid NUMERIC, Name TEXT, HomeWorld INTEGER);
            INSERT INTO CidMap VALUES (1, 'synthetic', 2);
            CREATE TABLE NpcSales(Cid, Item, Price, Quantity, UnixTime);
            INSERT INTO NpcSales VALUES(1, 999, 1, 1, 2);
            INSERT INTO ShopPurchases VALUES (1, '', 999, 51, 3, 1, 2000);
        """)
        # Two MessagePack items. First received HQ, second given away.
        payload = bytes.fromhex("9292ce000f4241039202ff")
        live.execute("INSERT INTO Trades VALUES (1, 2, 3000, 0, ?)", (payload,))
        live.commit()
        result = cashflow.read(tmp_path)
        assert result.status.recognised
        assert result.status.last_update_ts == int(Path(str(path) + "-wal").stat().st_mtime)
        assert result.records.characters == {1: ("synthetic", 2)}
        (trade,) = [e for e in result.records.events if e.kind == LedgerEventKind.TRADE_IN]
        assert (trade.item.id, trade.item.hq, trade.qty, trade.ts, trade.ppu) == (
            1,
            True,
            3,
            3,
            None,
        )
        (mannequin,) = [e for e in result.records.events if e.item.id == 999]
        assert mannequin.stack_gil == 153
        live.execute("ALTER TABLE GilRecords RENAME COLUMN GilPlayer TO Other")
        live.commit()
        invalid = cashflow.read(tmp_path)
        assert (
            invalid.status.warning == "CashFlow format not recognised"
            and not invalid.records.events
        )
