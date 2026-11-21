"""Explicit local build smoke test while the F2 desktop entry is unavailable."""

import argparse
from pathlib import Path

from gilhound import __version__
from gilhound.updater._layout import acknowledge_startup

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--launcher-pid", type=int)
    parser.add_argument("--ready-marker", type=Path)
    args = parser.parse_args()
    acknowledge_startup(__version__, args.ready_marker)
    with Path("../stub-starts.txt").open("a") as log:
        print(__version__, file=log)

