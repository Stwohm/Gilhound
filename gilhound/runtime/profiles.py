"""Versioned scan and scheduled-snipe profiles in the user's data directory."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from gilhound.models import ScanParams, ScanPreset, UserSettings

BUILTINS = {
    "Flip": ScanPreset.FLIP,
    "High margin": ScanPreset.HIGH_MARGIN,
    "Volume": ScanPreset.VOLUME,
    "Snipe": ScanPreset.SNIPE,
}
type Library = Literal["scan", "snipes"]


class Profile(BaseModel):
    params: ScanParams

    @model_validator(mode="after")
    def distinct_tiers(self) -> "Profile":
        tiers = self.params.freshness_tiers or []
        names = [t.name.strip().casefold() for t in tiers]
        ages = {t.max_age_h for t in tiers}
        if "" in names or len(set(names)) != len(names) or len(ages) != len(names):
            raise ValueError("Freshness tiers need unique names and ages.")
        tiers.sort(key=lambda t: t.max_age_h)
        return self


def describe(profile: Profile) -> list[str]:
    p = profile.params
    sentences = [f"Finds items in {p.buy_region} to sell on {p.home_world}."]
    for window in p.windows:
        sentences.append(
            f"Searches the last {window.hours_ago} hours for at least {window.min_sales} sales, "
            f"an average price of at least {window.min_desired_avg_ppu:,} gil and candidate "
            f"profit of at least {window.min_profit_amount:,} gil at {window.preferred_roi}% ROI."
        )
    sentences.append(
        f"Requires at least {p.min_history_sales} sales in 7 days, "
        f"{p.min_distinct_buyers} distinct buyers, sales on at least {p.min_days_with_sales} "
        f"days and at least {p.min_units_day:g} units/day."
    )
    profit = f"at least {p.min_profit:,} gil" if p.min_profit else "positive"
    sentences.append(
        f"Profit per unit must be {profit} after {p.buyer_tax * 100:g}% buyer tax "
        f"and {p.seller_tax * 100:g}% seller tax and travel costs, with sellable quantity "
        f"capped at {p.sellable_days:g} days of sales."
    )
    if p.max_top_buyer_share < 1:
        sentences.append(f"The largest buyer may buy up to {p.max_top_buyer_share * 100:g}%.")
    for label, limit in (("Listing data", p.max_data_age_h), ("The latest sale", p.max_sale_age_h)):
        if limit is not None:
            sentences.append(f"{label} must be less than {limit:g} hours old.")
    if p.preset == ScanPreset.SNIPE and (rule := p.snipe_rule):
        sentences.append(
            f"Snipe alerts require a listing at or below {rule.max_home_median_ratio * 100:g}% "
            f"of the {p.home_world} 7-day median, at least {rule.min_profit:,} gil profit per "
            f"unit, at least {rule.min_distinct_buyers_7d} buyers, a sale less than "
            f"{rule.max_last_sale_age_h:g} hours ago and a top buyer share of at most "
            f"{rule.max_top_buyer_share * 100:g}%."
        )
        lower = 0
        for tier in p.freshness_tiers or []:
            channel = "app and Discord when enabled" if tier.send_discord else "app only"
            sentences.append(
                f"{tier.name.capitalize()} listings aged {lower:g} to under "
                f"{tier.max_age_h:g} hours appear in the {channel}."
            )
            lower = tier.max_age_h
        if not p.freshness_tiers:
            sentences.append("No freshness tiers accept listings.")
    return sentences


class ProfileList(BaseModel):
    profiles: dict[str, Profile]
    defaults: dict[str, Profile]
    active: str
    descriptions: dict[str, list[str]]


class ProfileChange(BaseModel):
    action: Literal["create", "save", "rename", "delete", "reset", "activate"]
    name: str = Field(min_length=1, max_length=80)
    new_name: str | None = Field(default=None, max_length=80)
    profile: Profile | None = None


class Profiles:
    def __init__(self, root: Path) -> None:
        self.path = root / "profiles.json"

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_bytes())
        except FileNotFoundError:
            return {"version": 1}
        if data.get("version", 1) != 1:
            raise ValueError("Unsupported profiles file version.")
        return data

    def list(self, library: Library, settings: UserSettings) -> ProfileList:
        data = self._read()
        shipped = UserSettings(home_world=settings.home_world, buy_region=settings.buy_region)
        defaults = {}
        for name, strategy in BUILTINS.items():
            if library == "scan" or strategy == ScanPreset.SNIPE:
                defaults[name] = Profile(
                    params=ScanParams(
                        preset=strategy,
                        home_world=settings.home_world,
                        buy_region=settings.buy_region,
                        travel_cost=settings.travel_cost,
                        seller_tax=0.05,
                        snipe_rule=shipped.snipe_rule if strategy == ScanPreset.SNIPE else None,
                        freshness_tiers=shipped.freshness_tiers
                        if strategy == ScanPreset.SNIPE
                        else None,
                    )
                )
        profiles = defaults.copy()
        if "Snipe" not in data.get(library, {}):
            profiles["Snipe"] = Profile(
                params=defaults["Snipe"].params.model_copy(
                    update={
                        "snipe_rule": settings.snipe_rule,
                        "freshness_tiers": settings.freshness_tiers,
                    }
                )
            )
        for name, raw in data.get(library, {}).items():
            params = raw.get("params", {})
            strategy = params.get("preset", BUILTINS.get(name, ScanPreset.FLIP))
            base = next(p.params for p in defaults.values() if p.params.preset == strategy)
            profiles[name] = Profile(params=ScanParams.model_validate(base.model_dump() | params))
            p = profiles[name].params
            if p.preset == ScanPreset.SNIPE:
                if p.snipe_rule is None:
                    p.snipe_rule = settings.snipe_rule
                if p.freshness_tiers is None:
                    p.freshness_tiers = settings.freshness_tiers
        active = data.get("active", "Snipe") if library == "snipes" else "Flip"
        if active not in profiles:
            active = "Snipe" if library == "snipes" else "Flip"
        return ProfileList(
            profiles=profiles, defaults=defaults, active=active,
            descriptions={name: describe(profile) for name, profile in profiles.items()},
        )

    def change(
        self, library: Library, change: ProfileChange, settings: UserSettings
    ) -> ProfileList:
        # Serialize changes from independent desktop/API processes as well as threads.
        with closing(sqlite3.connect(self.path.with_name("gilhound.db"))) as db, db:
            db.execute("BEGIN IMMEDIATE")
            state = self.list(library, settings)
            data = self._read()
            entries = data.setdefault(library, {})
            name, action = change.name.strip(), change.action
            if action != "create" and name not in state.profiles:
                raise ValueError("Unknown profile.")
            if action in {"create", "rename"}:
                target = name if action == "create" else (change.new_name or "").strip()
                if not target or target.casefold() in {n.casefold() for n in state.profiles}:
                    raise ValueError("Choose a unique, non-empty profile name.")
            if action in {"rename", "delete"} and name in state.defaults:
                raise ValueError("Built-in profiles must retain their names.")
            if action in {"save", "create"}:
                profile = change.profile
                if profile is None:
                    raise ValueError("Supply a profile to save.")
                if (library == "snipes" and profile.params.preset != ScanPreset.SNIPE) or (
                    name in state.defaults and profile.params.preset != BUILTINS[name]
                ):
                    raise ValueError("Invalid strategy for this profile.")
                entries[name] = profile.model_dump()
            elif action == "reset":
                strategy = state.profiles[name].params.preset
                default = next(p for p in state.defaults.values() if p.params.preset == strategy)
                entries[name] = default.model_dump()
            elif action == "rename":
                entries[target] = entries.pop(name)
            elif action == "delete":
                del entries[name]
            if library == "snipes":
                if action in {"save", "create", "activate", "reset"}:
                    data["active"] = name
                elif name == state.active:
                    data["active"] = target if action == "rename" else "Snipe"
            with NamedTemporaryFile(dir=self.path.parent, delete=False) as stream:
                tmp = Path(stream.name)
            try:
                tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
                tmp.replace(self.path)
            finally:
                tmp.unlink(missing_ok=True)
            return self.list(library, settings)
