"""Application services shared by HTTP, desktop and CLI."""

import time
from collections.abc import Callable
from pathlib import Path
from threading import Lock

from gilhound.ingest import discover, read_plugins
from gilhound.ledger import Ledger, build_ledger
from gilhound.market.presets import run_scan
from gilhound.market.retainer import run_retainer_scan
from gilhound.market.snipe import evaluate
from gilhound.models import (
    RetainerScanParams,
    RetainerScanResult,
    ScanParams,
    ScanPreset,
    ScanResult,
    SnipeHit,
    SnipeRule,
    SourceStatus,
    UserSettings,
)

from .diagnostics import LOG, archive, start_logging
from .discord import DeliveryError, Discord
from .scheduler import Scheduler
from .store import History, SettingsStore, data_dir


class Runtime:
    def __init__(self, root: Path | None = None, plugins: Path | None = None) -> None:
        self.root = root or data_dir()
        self.plugins = plugins
        self.settings = SettingsStore(self.root / "settings.json")
        self.history = History(self.root / "gilhound.db")
        self.discord = Discord()
        self.scan_lock = Lock()
        self.scheduler = Scheduler(
            lambda: self.settings.get().scan_interval_min * 60, self.run_snipes
        )
        self.handler = start_logging(self.root / "logs")
        LOG.info("Runtime started")

    def close(self) -> None:
        self.discord.stopped.set()
        self.scheduler.stop()
        LOG.info("Runtime stopped")
        LOG.removeHandler(self.handler)
        self.handler.close()

    def save_settings(self, settings: UserSettings) -> UserSettings:
        saved = self.settings.save(settings)
        self.scheduler.wake.set()
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

    def scan(self, params: ScanParams) -> ScanResult:
        if params.preset == ScanPreset.SNIPE:
            with self.scan_lock:
                result, _ = self._snipe_scan(params, self.settings.get())
                return result
        result = run_scan(params, clock=time.time)
        self.history.save_scan(result)
        LOG.info("Scan completed")
        return result

    def retainer(
        self, params: RetainerScanParams, progress: Callable[[int, int], None] | None = None
    ) -> RetainerScanResult:
        settings = self.settings.get()
        return run_retainer_scan(
            params,
            clock=time.time,
            progress=progress,
            home_world=settings.home_world,
            buy_region=settings.buy_region,
        )

    def _snipe_scan(
        self, params: ScanParams, settings: UserSettings
    ) -> tuple[ScanResult, list[SnipeHit]]:
        result = run_scan(params, clock=time.time)
        hits = evaluate(
            result.rows,
            SnipeRule(max_top_buyer_share=0.5),
            settings.freshness_tiers,
            result.finished_ts,
        )
        result.rows = list(hits)
        self.history.save_scan(result)
        LOG.info("Scan completed")
        return result, hits

    def run_snipes(self) -> list[SnipeHit]:
        with self.scan_lock:
            settings = self.settings.get()
            _, hits = self._snipe_scan(self.params(ScanPreset.SNIPE), settings)
            self.history.record_hits(hits)
            for hit in hits:
                try:
                    self.discord.notify(settings, hit, self.history)
                except DeliveryError:
                    LOG.warning("Discord delivery failed")
            return self.history.hits(current=True)

    def sources(self) -> list[SourceStatus]:
        return discover(self.plugins)

    def ledger(self) -> Ledger:
        return build_ledger(read_plugins(self.plugins))

    def diagnostics(self) -> bytes:
        return archive(self.root / "logs", self.settings.get(), self.sources())
