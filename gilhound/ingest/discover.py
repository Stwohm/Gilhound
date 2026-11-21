"""Load a consistent batch per plugin. Each plugin fails independently."""

import os
from datetime import tzinfo
from pathlib import Path

from gilhound.models import SourceStatus

from . import allagan_market, allagan_tools, cashflow, restocky
from .common import Source


def read_plugins(root: Path | None = None, *, local_tz: tzinfo | None = None) -> list[Source]:
    if root is None:
        root = Path(os.environ.get("APPDATA", Path.home())) / "XIVLauncher" / "pluginConfigs"
    return [
        cashflow.read(root / "CashFlow"),
        allagan_market.read(root / "AllaganMarket", local_tz),
        allagan_tools.read(root / "InventoryTools"),
        restocky.read(root / "Restocky"),
    ]


def discover(root: Path | None = None) -> list[SourceStatus]:
    return [source.status for source in read_plugins(root)]
