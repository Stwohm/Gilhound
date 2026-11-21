"""User-owned cost overrides and manually recorded acquisitions."""

import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from pydantic import BaseModel, Field

from gilhound.models import EpochSeconds, GilCost, ItemRef


class ItemAdjustment(BaseModel):
    item: ItemRef
    unknown_unit_cost: GilCost | None = None
    personal: bool = False
    gathered: bool = False


class Acquisition(BaseModel):
    item: ItemRef
    qty: int = Field(gt=0)
    ts: EpochSeconds
    kind: Literal["opening", "trade_in"] = "opening"
    ppu: GilCost | None = None


class ManualAdjustments(BaseModel):
    items: list[ItemAdjustment] = Field(default_factory=list)
    acquisitions: list[Acquisition] = Field(default_factory=list)

    @staticmethod
    def path(directory: Path | None) -> Path:
        if directory is None:
            directory = Path(os.environ.get("APPDATA", Path.home())) / "Gilhound"
        return directory / "manual-adjustments.json"

    @classmethod
    def load(cls, directory: Path | None = None) -> "ManualAdjustments":
        try:
            return cls.model_validate_json(cls.path(directory).read_bytes())
        except FileNotFoundError:
            return cls()

    def save(self, directory: Path | None = None) -> None:
        path = self.path(directory)
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temp = Path(stream.name)
        try:
            # Preserve omission so existing cost and personal edits can still auto-detect.
            omitted_gathered = {
                index: {"gathered"}
                for index, item in enumerate(self.items)
                if "gathered" not in item.model_fields_set
            }
            temp.write_text(
                self.model_dump_json(indent=2, exclude={"items": omitted_gathered}),
                encoding="utf-8",
            )
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
