"""Export only application lifecycle events and structured, non-personal status."""

import io
import json
import logging
import re
import zipfile
from logging.handlers import RotatingFileHandler
from pathlib import Path

from gilhound import __version__
from gilhound.models import SourceStatus, UserSettings

WEBHOOK = re.compile(
    r"(?:https?://)?(?:[\w-]+\.)?discord(?:app)?\.com/api/webhooks/[^\s\"'<>]+", re.IGNORECASE
)
LOG = logging.getLogger("gilhound.runtime.events")
EVENTS = {
    "Runtime started",
    "Runtime stopped",
    "Scan completed",
    "Snipe scan failed",
    "Discord delivery failed",
}


class RedactWebhook(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = WEBHOOK.sub("[redacted]", record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = WEBHOOK.sub(
                "[redacted]", logging.Formatter().formatException(record.exc_info)
            )
        if record.stack_info:
            record.stack_info = WEBHOOK.sub("[redacted]", record.stack_info)
        return True


def start_logging(root: Path) -> RotatingFileHandler:
    root.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        root / "gilhound.log", maxBytes=256_000, backupCount=3, encoding="utf-8", delay=True
    )
    handler.addFilter(RedactWebhook())
    handler.setFormatter(logging.Formatter("%(created).0f %(levelname)s %(message)s"))
    LOG.setLevel(logging.INFO)
    LOG.propagate = False
    LOG.addHandler(handler)
    return handler


def archive(root: Path, settings: UserSettings, sources: list[SourceStatus]) -> bytes:
    data = settings.model_dump(include={
        "home_world": True,
        "buy_region": True,
        "history_days": True,
        "auto_snipe_enabled": True,
        "scan_interval_min": True,
        "discord_notifier": True,
        "dedup_cooldown_h": True,
        "snipe_rule": {
            "max_home_median_ratio", "min_profit", "min_distinct_buyers_7d",
            "max_last_sale_age_h", "max_top_buyer_share",
        },
        "freshness_tiers": {"__all__": {"name", "max_age_h", "send_discord"}},
    })
    # Labels are user-editable. Preserve numeric settings and known public defaults.
    for key, names in (("home_world", {"Cerberus"}), ("buy_region", {"Europe", "Chaos", "Light"})):
        if data[key] not in names:
            data[key] = "[custom]"
    data["partner_retainer_count"] = len(settings.partner_retainers)
    data["travel_cost"] = {
        k if k in {"Chaos", "Light"} else f"custom-{i}": v
        for i, (k, v) in enumerate(settings.travel_cost.items())
    }
    for tier in data["freshness_tiers"]:
        if tier["name"] not in {"fresh", "stale"}:
            tier["name"] = "[custom]"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as output:
        output.writestr("version.txt", __version__)
        output.writestr("settings.json", json.dumps(data))
        output.writestr(
            "sources.json", json.dumps([s.model_dump(exclude={"warning"}) for s in sources])
        )
        for path in sorted(root.glob("gilhound.log*")):
            if path.name not in {"gilhound.log", *(f"gilhound.log.{n}" for n in range(1, 4))}:
                continue
            safe = []
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                fields = line.split(" ", 2)
                if (
                    len(fields) == 3
                    and fields[0].isascii()
                    and fields[0].isdigit()
                    and fields[1] in {"INFO", "WARNING", "ERROR"}
                    and fields[2] in EVENTS
                ):
                    safe.append(line + "\n")
            output.writestr(f"logs/{path.name}", "".join(safe))
    return buffer.getvalue()
