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


class ProfileList(BaseModel):
    profiles: dict[str, Profile]
    defaults: dict[str, Profile]
    active: str


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
        active = data.get("active", "Snipe") if library == "snipes" else "Flip"
        if active not in profiles:
            active = "Snipe" if library == "snipes" else "Flip"
        return ProfileList(profiles=profiles, defaults=defaults, active=active)

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
