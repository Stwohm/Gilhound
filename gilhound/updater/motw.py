"""Remove download-zone streams within an installed Windows bundle."""

import os
import stat
from pathlib import Path


def unblock(path: Path) -> int:
    if os.name != "nt" or path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return 0
    if path.is_dir():
        return sum(unblock(child) for child in path.iterdir())
    try:
        os.remove(f"{path}:Zone.Identifier")
    except FileNotFoundError:
        return 0
    return 1
