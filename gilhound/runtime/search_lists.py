"""Named item searches stored in the local Gilhound data directory."""

import time
from pathlib import Path
from tempfile import NamedTemporaryFile

from gilhound.market.budget import try_lock
from gilhound.models import SavedSearch, SearchListChange, SearchLists


class SearchListNotFound(LookupError):
    pass


class SearchListStore:
    def __init__(self, root: Path) -> None:
        self.path = root / "search-lists.json"

    def list(self) -> SearchLists:
        try:
            return SearchLists.model_validate_json(self.path.read_bytes(), extra="forbid")
        except FileNotFoundError:
            return SearchLists()
        except (OSError, ValueError):
            return SearchLists(warning="Could not read search-lists.json. Saved lists are empty.")

    def get(self, name: str) -> SavedSearch:
        library = self.list()
        key = self._key(library.lists, name.strip())
        if key is None:
            raise SearchListNotFound(library.warning or "Saved search list not found.")
        return library.lists[key]

    @staticmethod
    def _key(lists: dict[str, SavedSearch], name: str) -> str | None:
        return next((key for key in lists if key.casefold() == name.casefold()), None)

    def change(self, change: SearchListChange) -> SearchLists:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while (lock := try_lock(self.path.with_suffix(".lock"))) is None:
            time.sleep(0.01)
        with lock:
            lists = self.list().lists
            name = change.name.strip()
            existing = self._key(lists, name)
            if not name:
                raise ValueError("Enter a list name.")
            if change.action != "create" and existing is None:
                raise SearchListNotFound("Saved search list not found.")
            if change.action == "delete":
                del lists[existing]
            elif change.action == "rename":
                new_name = (change.new_name or "").strip()
                if not new_name:
                    raise ValueError("Enter a new list name.")
                if self._key(lists, new_name) not in (None, existing):
                    raise ValueError("A list with that name already exists.")
                lists[new_name] = lists.pop(existing)
            else:
                if change.action == "create" and existing is not None:
                    raise ValueError("A list with that name already exists.")
                if change.search is None or any(
                    entry.item_id <= 0 for entry in change.search.entries
                ):
                    raise ValueError("A saved list needs valid item IDs.")
                lists[existing or name] = change.search
            result = SearchLists(lists=lists)
            with NamedTemporaryFile(dir=self.path.parent, delete=False) as stream:
                temp = Path(stream.name)
            try:
                temp.write_text(result.model_dump_json(exclude={"warning"}), encoding="utf-8")
                temp.replace(self.path)
            finally:
                temp.unlink(missing_ok=True)
            return result
