import io
import json
import logging
import secrets
import threading
import time
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

from pydantic import SecretStr

from gilhound import __version__
from gilhound.market.snipe import discord_embed, embed_length
from gilhound.models import ScanPreset, ScanResult, SnipeHit, UserSettings
from gilhound.runtime import Runtime
from gilhound.runtime.diagnostics import LOG, WEBHOOK, RedactWebhook, archive
from gilhound.runtime.discord import Discord
from gilhound.runtime.partner import decode
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


def test_explicit_partner_post(client, monkeypatch) -> None:
    config = settings()
    config.discord_notifier = False
    config.home_world = "Private world"
    monkeypatch.setattr("gilhound.runtime.store.SettingsStore.get", lambda self: config)
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value.status = 204
    monkeypatch.setattr("gilhound.runtime.discord.build_opener", lambda *args: opener)
    response = client.post("/api/partner/discord", json={"sender": "Sample", "items": []})
    assert response.status_code == 200
    (request,) = opener.open.call_args.args
    assert request.full_url == config.webhook_url.get_secret_value()
    body = json.loads(request.data)
    title, fence, code, close = body["content"].splitlines()
    assert (title, fence, close) == ("Gilhound partner share", "```", "```")
    assert body == {"content": body["content"], "allowed_mentions": {"parse": []}}
    share = decode(code)
    assert share.model_dump(exclude={"created_ts"}) == {
        "version": 2, "sender": "Sample", "items": [], "listings": [], "retainers": [],
    }
    # Large shares arrive as a single importable attachment.
    items = [{"id": i + 1, "hq": False, "note": secrets.token_hex(75)} for i in range(100)]
    response = client.post("/api/partner/discord", json={"sender": "Sample", "items": items})
    assert response.status_code == 200
    (request,) = opener.open.call_args.args
    assert b'filename="partner.gilhound-share"' in request.data
    (attachment,) = [line for line in request.data.splitlines() if line.startswith(b"GH2.")]
    assert len(decode(attachment.decode()).items) == len(items)
    assert config.webhook_url.get_secret_value().encode() not in request.data


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
    sender.notify_many(config.model_copy(update={"discord_notifier": False}), [row], db)
    muted = row.model_copy(update={"tier": row.tier.model_copy(update={"send_discord": False})})
    sender.notify_many(config, [muted], db)
    partner = row.model_copy(update={"partner": "manual"})
    assert sender.notify_many(config, [partner], db) == 0
    assert "🤝 partner item" in discord_embed(partner, "Cerberus", "🪙")["description"]
    assert not calls
    assert sender.notify_many(config, [row], db) == 0
    assert db.hits()[0].notified_ts is None
    sender.notify_many(config, [row], db)
    reopened = History(db.path)
    reopened.record_hits([row.model_copy(update={"first_seen_ts": 10})])
    sender.notify_many(config, [row], reopened)
    assert len(calls) == 3 and calls[-1]["embeds"] == [discord_embed(row, "Cerberus", "🪙")]
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
    config.discord_coin_emoji = "private label"
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
        now = int(time.time())
        stamps = [now - 91 * 86400, now - 1, now]
        for i in stamps:
            runtime.history.save_scan(
                ScanResult(
                    params=runtime.params(ScanPreset.FLIP), rows=[], started_ts=i, finished_ts=i
                )
            )
        assert [r.started_ts for r in runtime.history.entries()] == stamps[:0:-1]
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


def test_scheduler_pause_resume_and_live_interval() -> None:
    ran = threading.Event()
    delay: float | None = 60
    scheduler = Scheduler(lambda: delay, ran.set)
    scheduler.start()
    try:
        delay = None
        scheduler.configure()
        assert scheduler.next_run is None
        assert not ran.wait(0.05)
        delay = 60
        scheduler.configure()
        assert ran.wait(1)
        ran.clear()
        delay = 0.02
        scheduler.configure()
        assert ran.wait(1)
        delay = None
        scheduler.configure()
        ran.clear()
        assert not ran.wait(0.1)
    finally:
        scheduler.stop()


def test_embed_and_batch_snapshots(tmp_path: Path) -> None:
    config = settings()
    config.discord_coin_emoji = "<:gil:123>"
    row = hit()
    row.item.hq = True
    expected = {
        "title": "Item HQ",
        "url": "https://universalis.app/market/1?server=Cerberus",
        "description": "```\n/li World market\n```",
        "color": 0x2ECC71,
        "footer": {"text": f"Gilhound v{__version__} · fresh"},
        "timestamp": "1970-01-01T00:00:05+00:00",
        "fields": [
            {"name": name, "value": value, "inline": True}
            for name, value in [
                ("🛒 Buy", "1 <:gil:123> × 2\nWorld (DC)"),
                ("📊 Cerberus median", "12 <:gil:123>"),
                ("🏷️ Lowest on Cerberus", "Unknown"),
                ("💰 Profit", "+9 <:gil:123> / unit\n+18 <:gil:123> total"),
                ("📈 Sells", "2.0/day · 3 buyers · last Cerberus sale 1.0 h ago · top buyer 40%"),
                ("🕒 Buy listing last seen", "0 min (fresh)"),
            ]
        ],
    }
    messages = []

    def capture(url: str, payload: dict) -> tuple[int, bytes]:
        messages.append(payload)
        return 204, b""

    sender = Discord(capture)
    history = History(tmp_path / "db")
    rows = [row.model_copy(update={"listing_key": str(i)}) for i in range(12)]
    history.record_hits(rows)
    assert sender.notify_many(config, rows[:1], history) == 1
    assert messages.pop() == {"embeds": [expected], "allowed_mentions": {"parse": []}}
    config.dedup_cooldown_h = 0
    assert sender.notify_many(config, rows, history) == 12
    assert messages == [
        {
            "allowed_mentions": {"parse": []},
            "embeds": [
                expected if i == 0 else {**expected, "url": expected["url"] + f"#listing-{i}"}
                for i in range(count)
            ],
        }
        for count in (10, 2)
    ]
    messages.clear()
    row.item.icon_url = "https://v2.xivapi.com/api/asset?path=ui/icon/sample.tex&format=png"
    row.item.name = "@everyone" + "💰" * 300
    config.home_world = "W" * 400
    config.discord_coin_emoji = "🪙" * 80
    assert sender.notify_many(config, rows, history) == 12
    assert len(messages) > 2
    for message in messages:
        assert sum(map(embed_length, message["embeds"])) <= 6000
        for embed in message["embeds"]:
            assert embed["thumbnail"]["url"] == row.item.icon_url
            assert len(embed["title"].encode("utf-16-le")) <= 512
            assert "@everyone" not in embed["title"]
            assert all(len(field["name"]) <= 256 for field in embed["fields"])
    sender.test(config)
    assert messages[-1]["embeds"][0]["title"].startswith("Test")

def test_scheduler_interrupts_market_retry(monkeypatch, tmp_path) -> None:
    from urllib import request

    from gilhound.market.http import HttpClient

    monkeypatch.setenv("APPDATA", str(tmp_path))
    entered = threading.Event()
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(None)
        entered.set()
        raise TimeoutError

    monkeypatch.setattr(request, "urlopen", unavailable)
    scheduler = Scheduler(lambda: 0.01, lambda: HttpClient().request("https://example.invalid"))
    scheduler.start()
    try:
        assert entered.wait(2)
    finally:
        start = time.monotonic()
        scheduler.stop()
    assert time.monotonic() - start < 1
    assert not scheduler.thread.is_alive()
    assert len(calls) == 1
