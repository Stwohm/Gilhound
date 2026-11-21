"""Check staged blobs or the tracked tree for forbidden strings."""

import argparse
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_PATTERNS = REPO_ROOT / ".secrets-patterns.local"
# Build each search string at runtime so the scanner can scan its own source.
WEBHOOK_PATTERNS = tuple(host + "/api/webhooks" for host in ("discord.com", "discordapp.com"))


def git_output(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=REPO_ROOT)


def check_staged() -> bool:
    try:
        local_patterns = LOCAL_PATTERNS.read_bytes().removeprefix(b"\xef\xbb\xbf").splitlines()
    except FileNotFoundError:
        local_patterns = []
    local_patterns = [pattern for pattern in local_patterns if pattern]
    webhook_patterns = [pattern.encode("ascii") for pattern in WEBHOOK_PATTERNS]

    changes = git_output(
        "diff", "--cached", "--raw", "--no-renames", "--no-abbrev", "--diff-filter=AMT", "-z"
    ).split(b"\0")
    for metadata in changes[:-1:2]:
        _, mode, _, oid, _ = metadata.split()
        if mode == b"160000":  # A submodule entry points to a commit, not a blob.
            continue
        blob = git_output("cat-file", "blob", oid.decode("ascii"))
        if any(pattern in blob for pattern in local_patterns):
            return True
        folded = blob.lower()
        if any(pattern in folded for pattern in webhook_patterns):
            return True
    return False


def check_tracked() -> bool:
    patterns = [arg for pattern in WEBHOOK_PATTERNS for arg in ("-e", pattern)]
    command = ["git", "grep", "--quiet", "--text", "--fixed-strings", "--ignore-case", *patterns]
    result = subprocess.run(command, cwd=REPO_ROOT, check=False)
    if result.returncode not in (0, 1):
        result.check_returncode()
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracked", action="store_true", help="Check tracked files for webhooks.")
    args = parser.parse_args()
    found = check_tracked() if args.tracked else check_staged()
    if found:
        print("A forbidden webhook or private pattern was found.")
    return int(found)


if __name__ == "__main__":
    raise SystemExit(main())
