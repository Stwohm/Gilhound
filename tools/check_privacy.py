"""Scan F0-B paths without printing private patterns or matching text."""

import argparse
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATTERNS = Path(
    os.environ.get("GILHOUND_PATTERNS", "D:/Claude/Gilhound/.secrets-patterns.local")
)
OWNED = (
    "legacy",
    "gilhound/sources/fixtures.py",
    "gilhound/sources/__init__.py",
    "tools",
    "tests/fixtures",
    "tests/golden",
    "tests/test_golden.py",
    "docs/research",
)


def load_patterns(path: Path) -> list[re.Pattern[str]]:
    patterns = [re.compile(r"discord\.com/api/" + "webhooks", re.IGNORECASE)]
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        pattern = line.strip()
        if pattern and not pattern.startswith("#"):
            try:
                patterns.append(re.compile(pattern, re.IGNORECASE))
            except re.error:
                raise ValueError(f"Invalid privacy regex on line {number}") from None
    return patterns


def scan(paths: list[Path], patterns: list[re.Pattern[str]]) -> tuple[int, int]:
    hits = files = 0
    for path in paths:
        entries = path.rglob("*") if path.is_dir() else (path,)
        for file in entries:
            file_count = 0
            if not file.is_file() or "__pycache__" in file.parts:
                continue
            files += 1
            content = file.read_bytes().decode("utf-8", errors="replace")
            for pattern in patterns:
                file_count += sum(1 for _ in pattern.finditer(content))
            hits += file_count
            if file_count:
                print(f"Privacy match in scanned file {files} ({file_count} hits)")
    return hits, files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patterns", type=Path, default=DEFAULT_PATTERNS)
    parser.add_argument("paths", type=Path, nargs="*")
    args = parser.parse_args()
    hits, files = scan(
        args.paths or [ROOT / name for name in OWNED], load_patterns(args.patterns)
    )
    print(f"Privacy scan: {hits} hits across {files} files.")
    raise SystemExit(bool(hits))


if __name__ == "__main__":
    main()
