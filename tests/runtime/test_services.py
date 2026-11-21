import io
import logging
import secrets
import threading
import zipfile
from pathlib import Path

import pytest
from pydantic import SecretStr

from gilhound.market.snipe import discord_payload
from gilhound.models import ScanPreset, ScanResult, SnipeHit, UserSettings
from gilhound.runtime import Runtime
from gilhound.runtime.diagnostics import LOG, WEBHOOK, RedactWebhook, archive
from gilhound.runtime.discord import DeliveryError, Discord
from gilhound.runtime.scheduler import Scheduler
from gilhound.runtime.store import History


def hit() -> SnipeHit:
    return SnipeHit.model_validate(
        dict(
            item=dict(id=1, name="Item", hq=False),
            buy_world="World",
            buy_dc="DC",
            buy_ppu=1,
            buy_qty_at_price=2,
            home_median_7d=12,
            home_lowest=None,
            sellable_qty=2,
            profit_per_unit=9,
            total_profit_capped=18,
            est_wait_days=None,
            data_age_h=0,
            volume_proof=dict(
                units_per_day=2,
                distinct_buyers_7d=3,
                last_sale_age_h=1,
                top_buyer_share=0.4,
                days_with_sales=2,
            ),
            tier=dict(name="fresh", max_age_h=1, send_discord=True),
            listing_key="key",
            first_seen_ts=5,
        )
    )


def settings() -> UserSettings:
    return UserSettings(
        home_world="Cerberus",
        buy_region="Europe",
        discord_notifier=True,
        webhook_url=SecretStr(
            "https://" + "discord.com/api/" + "webhooks/1/" + secrets.token_urlsafe()
        ),
    )


def test_delivery_retry_tiers_and_persistent_cooldown(tmp_path: Path) -> None:
    db = History(tmp_path / "test.db")
    row = hit()
    db.record_hits([row])
    config = settings()
    calls, delays = [], []
    replies = iter([(500, b""), (429, b'{"retry_after":0.25}'), (204, b"")])

    def transport(url: str, body: dict) -> tuple[int, bytes]:
        assert url == config.webhook_url.get_secret_value()
        calls.append(body)
        return next(replies)

    sender = Discord(transport, delays.append)
    sender.notify(config.model_copy(update={"discord_notifier": False}), row, db)
    muted = row.model_copy(update={"tier": row.tier.model_copy(update={"send_discord": False})})
    sender.notify(config, muted, db)
    assert not calls
    with pytest.raises(DeliveryError):
        sender.notify(config, row, db)
    assert db.hits()[0].notified_ts is None
    sender.notify(config, row, db)
    reopened = History(db.path)
    reopened.record_hits([row.model_copy(update={"first_seen_ts": 10})])
    sender.notify(config, row, reopened)
    assert len(calls) == 3 and calls[-1] == discord_payload(row)
    assert delays == [0.25]
    (saved,) = reopened.hits()
    assert saved.first_seen_ts == 5 and saved.notified_ts
    reopened.record_hits([])
    assert reopened.hits(current=True) == []
    assert reopened.hits() == [saved]
    assert reopened.reserve(row.listing_key, 0) is not None
    assert reopened.reserve(row.listing_key, 0) is None


def test_redaction_and_diagnostics(tmp_path: Path) -> None:
    runtime = Runtime(tmp_path, tmp_path / "plugins")
    config = settings()
    url = config.webhook_url.get_secret_value()
    try:
        for test_url in (url, url.replace("discord.", "discordapp.")):
            record = logging.makeLogRecord({"msg": "failed %s", "args": (test_url,)})
            RedactWebhook().filter(record)
            assert test_url not in record.getMessage()
        LOG.warning("private label %s", url)
        payload = archive(tmp_path / "logs", config, runtime.sources())
        with zipfile.ZipFile(io.BytesIO(payload)) as zipped:
            assert set(zipped.namelist()) == {
                "settings.json",
                "sources.json",
                "version.txt",
                "logs/gilhound.log",
            }
            for name in zipped.namelist():
                content = zipped.read(name).decode()
                assert not WEBHOOK.search(content)
                assert "webhook_url" not in content and "private label" not in content
            assert b"Runtime started" in zipped.read("logs/gilhound.log")
        assert not WEBHOOK.search((tmp_path / "logs/gilhound.log").read_text())
    finally:
        runtime.close()


def test_settings_and_history(tmp_path: Path) -> None:
    runtime = Runtime(tmp_path, tmp_path / "plugins")
    try:
        config = runtime.settings.get()
        # Exercise secret persistence with an opaque marker.
        config.webhook_url = SecretStr("placeholder")
        runtime.save_settings(config)
        assert runtime.settings.get().webhook_url == config.webhook_url
        plain = UserSettings(home_world="World", buy_region="Region")
        assert runtime.save_settings(plain).webhook_url == config.webhook_url
        plain.webhook_url = None
        assert runtime.save_settings(plain).webhook_url is None
        runtime.history.limit = 2
        for i in range(3):
            runtime.history.save_scan(
                ScanResult(
                    params=runtime.params(ScanPreset.FLIP), rows=[], started_ts=i, finished_ts=i
                )
            )
        assert [r.finished_ts for r in runtime.history.scans()] == [2, 1]
    finally:
        runtime.close()


def test_scheduler_no_overlap_and_shutdown() -> None:
    entered, release, again = threading.Event(), threading.Event(), threading.Event()
    count = 0

    def work() -> None:
        nonlocal count
        count += 1
        if count == 1:
            entered.set()
            release.wait(2)
        else:
            again.set()

    scheduler = Scheduler(lambda: 0.01, work)
    scheduler.start()
    try:
        assert entered.wait(2)
        scheduler.start()
        assert not again.wait(0.05)
        release.set()
        assert again.wait(2)
    finally:
        release.set()
        scheduler.stop()
    assert not scheduler.thread.is_alive()
