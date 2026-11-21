# Legacy market scans

| Script | What it does |
| --- | --- |
| `flip_scan.py` | Finds Europe-to-home-world flips using Saddlebag candidates and the home world's recent median sale price. Ranks profit by an estimate of sellable quantity. |
| `hiscan2.py` | Looks for high-margin flips with multiple buyers and sales on several days. |
| `hiscan_vol.py` | Looks for flips with stronger sales volume and buyer diversity. |
| `snipe.py` | Looks for a cheap listing well below the next regional listing, then checks local profit and buyer count. |
| `restock.py` | Compares prices, recent sales and restock profitability for an embedded item selection. |
| `bigcheck.py` | Resolves an embedded selection of item names and checks regional listings, home listings and sale history. |

The Python files preserve the original scan logic and output. The three
scripts that import `flip_scan` now find it beside themselves. The two
scripts with personal item selections are skipped during recording and replay.
Their item IDs and names are public game catalog data.

## Fixture workflow

Run `python -B tools/record_fixtures.py` with `GILHOUND_FIXTURE_SALT` set
privately in the process environment. Supply script names to record a subset.
The recorder uses live network requests. Per Saddlebag response it keeps up
to eight candidates for flip and high-margin and up to 24 for volume and
snipe. The rank is descending Saddlebag profit estimate, then ascending item
ID. Selected rows are passed to the scripts in their original response order.
The recorder stages raw data in a temporary directory outside the worktree,
anonymizes it there and verifies an offline run before publishing the JSON
and stdout goldens. Replay freezes `time.time()` to the capture start and
fails the scan if any request was missing, even if the legacy script swallowed
the exception.

Run `python -B -m pytest tests/test_golden.py` to replay offline and
`python -B tools/check_privacy.py` to scan the F0-B paths. The tools read the
main worktree's local privacy pattern file by default. Use
`GILHOUND_PATTERNS` or `--patterns` to supply a different local path.
