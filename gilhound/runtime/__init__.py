"""Application services shared by HTTP, desktop and CLI."""

import time
from collections.abc import Callable
from pathlib import Path
from threading import Lock

from gilhound.ingest import discover, read_plugins
from gilhound.ingest.common import item_ref
from gilhound.ledger import ItemAdjustment, Ledger, ManualAdjustments, build_ledger
from gilhound.market.items import ItemCatalog
from gilhound.market.presets import run_scan
from gilhound.market.retainer import run_retainer_scan
from gilhound.market.snipe import evaluate
from gilhound.models import (
    ItemRef,
    RetainerScanParams,
    RetainerScanResult,
    ScanParams,
    ScanPreset,
    ScanResult,
    SnipeHit,
    SnipeStatus,
    SourceStatus,
    UserSettings,
)

from .bridge import Bridge
from .diagnostics import LOG, archive, start_logging
from .discord import Discord
from .profiles import Profiles
from .scheduler import Scheduler
from .store import History, SettingsStore, data_dir


class Runtime:
    def __init__(self, root: Path | None = None, plugins: Path | None = None) -> None:
        self.root = root or data_dir()
        self.profiles = Profiles(self.root)
        self.plugins = plugins
        self.bridge = Bridge(plugins)
        self.items = ItemCatalog(self.root)
        self.adjustment_lock = Lock()
        self.settings = SettingsStore(self.root / "settings.json")
        self.history = History(self.root / "gilhound.db", lambda: self.settings.get().history_days)
        self.discord = Discord()
        self.scan_lock = Lock()
        self.scheduler = Scheduler(self.snipe_interval, self.run_snipes)
        self.handler = start_logging(self.root / "logs")
        LOG.info("Runtime started")

    def close(self) -> None:
        self.discord.stopped.set()
        self.scheduler.stop()
        self.items.close()
        LOG.info("Runtime stopped")
        LOG.removeHandler(self.handler)
        self.handler.close()

    def snipe_interval(self) -> float | None:
        settings = self.settings.get()
        return settings.scan_interval_min * 60 if settings.auto_snipe_enabled else None

    def snipe_status(self) -> SnipeStatus:
        deadline = self.scheduler.next_run
        return self.history.last_run().model_copy(update={
            "running": self.scan_lock.locked(), "next_run_ts": int(deadline) if deadline else None,
        })

    def save_settings(self, settings: UserSettings) -> UserSettings:
        saved = self.settings.save(settings)
        self.scheduler.configure()
        return saved

    def params(self, preset: ScanPreset) -> ScanParams:
        settings = self.settings.get()
        return ScanParams(
            home_world=settings.home_world,
            buy_region=settings.buy_region,
            preset=preset,
            seller_tax=0.05,
            travel_cost=settings.travel_cost,
        )

    def scan(self, params: ScanParams, profile: str | None = None) -> ScanResult:
        if params.preset == ScanPreset.SNIPE:
            with self.scan_lock:
                result, _ = self._snipe_scan(params, self.settings.get(), profile)
                return result
        with self.history.attempt(params, profile):
            result = run_scan(params, clock=time.time)
            self.items.decorate(
                (r.item for r in result.rows), params.home_world, wait=True
            )
        self.history.save_scan(result, profile)
        LOG.info("Scan completed")
        return result

    def retainer(
        self, params: RetainerScanParams, progress: Callable[[int, int], None] | None = None,
        profile: str | None = None,
    ) -> RetainerScanResult:
        settings = self.settings.get()
        params = params.model_copy(update={"world": params.world or settings.home_world,
                                           "buy_region": params.buy_region or settings.buy_region})
        with self.history.attempt(params, profile) as started:
            result = run_retainer_scan(
                params,
                clock=time.time,
                progress=progress,
                marketable=self.items.marketable,
                home_world=settings.home_world,
                buy_region=settings.buy_region,
            )

            self.items.decorate((r.item for r in result.rows), settings.home_world, wait=True)
        self.history.save_scan(result, profile, started=started)
        return result

    def _snipe_scan(
        self, params: ScanParams, settings: UserSettings, profile: str | None = None
    ) -> tuple[ScanResult, list[SnipeHit]]:
        params = params.model_copy(update={
            "snipe_rule": params.snipe_rule or settings.snipe_rule,
            "freshness_tiers": settings.freshness_tiers if params.freshness_tiers is None
            else params.freshness_tiers,
        })
        with self.history.attempt(params, profile):
            result = run_scan(params, clock=time.time)
            hits = evaluate(
                result.rows,
                params.snipe_rule,
                params.freshness_tiers,
                result.finished_ts,
            )
            self.items.decorate((r.item for r in hits), params.home_world, wait=True)
            result.rows = list(hits)
        self.history.save_scan(result, profile)
        LOG.info("Scan completed")
        return result, hits

    def run_snipes(self) -> list[SnipeHit]:
        with self.scan_lock:
            settings = self.settings.get()
            profiles = self.profiles.list("snipes", settings)
            active = profiles.profiles[profiles.active]
            _, hits = self._snipe_scan(active.params, settings, profiles.active)
            self.history.record_hits(hits)
            current = self.history.hits(current=True)
            config = self.settings.get().model_copy(update={"home_world": active.params.home_world})
            if config.discord_notifier and config.webhook_url:
                self.items.decorate_icons([hit.item for hit in current if hit.tier.send_discord])
            notified = self.discord.notify_many(config, current, self.history)
            self.history.save_run(
                SnipeStatus(
                    last_run_ts=int(time.time()),
                    notified=notified,
                    fresh=sum(hit.tier.name.casefold() == "fresh" for hit in current),
                    stale=sum(hit.tier.name.casefold() == "stale" for hit in current),
                )
            )
            return self.history.hits(current=True)

    def sources(self) -> list[SourceStatus]:
        return discover(self.plugins)

    def ledger(self) -> Ledger:
        return build_ledger(read_plugins(self.plugins), ManualAdjustments.load(self.root))

    def hidden(self) -> list[ItemRef]:
        return [a.item for a in ManualAdjustments.load(self.root).items if a.personal]

    def set_personal(self, iid: int, hq: bool, personal: bool) -> None:
        with self.adjustment_lock:
            manual = ManualAdjustments.load(self.root)
            entry = next((a for a in manual.items if (a.item.id, a.item.hq) == (iid, hq)), None)
            if entry is None:
                entry = ItemAdjustment(item=item_ref(iid, hq))
                manual.items.append(entry)
            entry.personal = personal
            manual.save(self.root)

    def set_cost(self, iid: int, hq: bool, unit_cost: int) -> None:
        with self.adjustment_lock:
            manual = ManualAdjustments.load(self.root)
            for entry in manual.items:
                if (entry.item.id, entry.item.hq) == (iid, hq):
                    entry.unknown_unit_cost = unit_cost
                    break
            else:
                manual.items.append(
                    ItemAdjustment(item=item_ref(iid, hq), unknown_unit_cost=unit_cost)
                )
            manual.save(self.root)

    def diagnostics(self) -> bytes:
        return archive(self.root / "logs", self.settings.get(), self.sources())
