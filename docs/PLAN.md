# Gilhound — plan

Desktop app for FFXIV market-board flipping: scan for flips, run a periodic
snipe scan with Discord notifications, track positions from plugin data.
Used by humans (UI) and by AI agents (CLI with compact JSON).

Status: **F0 done, F1 next** (2026-11-21).

## Decisions

| Topic | Decision |
|---|---|
| Deployment | Desktop only (v1). A server-side snipe worker is a later, separate plan. |
| Repo | Public, `Stwohm/Gilhound`. No secrets or real user data ever committed. |
| UI language | English. |
| Users | Two users, both on Cerberus (Chaos). Home world and buy regions are per-user settings anyway. |
| Discord | One shared channel. Each client has a "Discord notifier" toggle (default: on for one user). Same listing is not re-sent within a cooldown (default 6 h). Bot-token cross-client dedup is a later option. |
| Agent access | `gilhound` CLI, compact JSON by default (`--top 20 --fields ...`). MCP server deferred. |

## Architecture

| Layer | Choice |
|---|---|
| Core | Python 3.12+ package `gilhound` (ports the legacy scripts in `D:\Claude\Tools\ffxiv-market`). |
| Models | pydantic v2 in `gilhound/models.py` — the contract. |
| Local API | FastAPI on `127.0.0.1` only, per-session token, `Host` header allowlist (`127.0.0.1`, `localhost`) against DNS rebinding, no CORS. |
| UI | React + Vite + TypeScript in `ui/`; types generated from `openapi.json` with `openapi-typescript`. |
| Window | pywebview (WebView2). Missing WebView2 → clear error dialog with install link. |
| Storage | SQLite + settings in `%APPDATA%\Gilhound`. Webhook URL lives only there; never logged, never in diagnostics. |
| Time | UTC everywhere internally (epoch seconds). Universalis mixes units (sales `timestamp` and listing `lastReviewTime` = seconds, `lastUploadTime`/`worldUploadTimes` = milliseconds; see `docs/research/universalis.md`) — normalised at the API-client boundary, with tests. Local time only for display. |
| Packaging | PyInstaller `--onedir`, shipped as zip (fewer AV false positives than `--onefile`). |
| Updates | GitHub Releases `latest`. Flow: download zip → verify sha256 → extract to `app-<version>/` → small launcher points to the new folder and restarts → old folder deleted on next start. |
| Release | `git tag vX.Y.Z` + push → GitHub Actions (windows) builds and publishes the release. |

## Plugin data (read-only, minimal manual input)

Root: `%APPDATA%\XIVLauncher\pluginConfigs`. Each reader validates schema first;
on mismatch it skips the source with a warning ("CashFlow format not
recognised") instead of crashing. UI shows per-source freshness ("last update:
3 days ago" also reveals the game is closed).

| Source | File | Used for |
|---|---|---|
| CashFlow | `CashFlow/DefaultDbV2.sqlite` | `ShopPurchases` (MB buys = cost), `RetainerSales`, `GilRecords` (cash). Read via `sqlite3` backup API into a temp copy (journal is `delete` today; WAL-safe anyway). |
| AllaganMarket | `AllaganMarket/SaleItems.csv`, `SoldItems.csv` | open listings, sales |
| Allagan Tools | `InventoryTools/inventories.csv` | stock in bags/retainers (container `12002` = retainer market) |
| Restocky | `Restocky/listings.*`, `competitors.csv`, `price-history.csv`, `cost-ledger.json` | listings, rivals, price history, cost basis |

Manual input only for: non-MB acquisitions (trades from a friend), and marking
an item "personal, not a flip".

### Ledger / FIFO rules
- HQ and NQ are different items.
- Partial sales and split stacks.
- Tax on both sides (verified F0-B, `docs/research/taxes.md`): buyer pays 5% on top of the listing (`tax` field), seller receives ~95% of gross. CashFlow `RetainerSales.Price` is already net stack proceeds — do not multiply by quantity or deduct tax again.
- Opening stock with unknown cost (plugin installed after purchase) → flagged, cost editable.
- Listing pulled back to inventory; items acquired by trade.
- Canonical truth = CashFlow raw records (tolerance ±1 gil). Hand-kept Flip Log is a sanity check only (±2%).

## Scans

Presets port the legacy scripts: `flip` (flip_scan), `high-margin` (hiscan2),
`volume` (hiscan_vol), `snipe` (snipe). Volume proof is mandatory in all:
units/day, distinct buyers in 7 d, last sale age, single-buyer dominance.
Profit is capped by estimated sellable quantity (e.g. velocity × 3 days), not
by the whole stack. Profit = sell × (1 − seller tax) − buy × (1 + buyer tax) −
travel cost. Travel cost: a per-DC fixed "hassle" gil threshold (Light visit),
user-configurable.

### Snipe defaults (all user-configurable)
- Europe (Chaos + Light) listing ≤ 50% of home-world 7-day median.
- Profit after taxes ≥ 5,000.
- ≥ 3 distinct buyers, last sale < 24 h, no single dominant buyer.
- **Freshness tiers** — every snipe shows its data age everywhere (UI and
  Discord message). Default: fresh (< 1 h) → Discord + app; stale (1–6 h) →
  app only, tagged "stale". The user can choose per tier whether it goes to
  Discord (e.g. send stale ones too, with the age shown). Thresholds come from
  the F0 listing-age measurement.
- Interval N minutes (default 5). Universalis WebSocket
  (`wss://universalis.app/api/ws`, BSON, `listings/add{world=..,item=..}`)
  exists; it reduces load, not staleness (events come from uploads). REST for
  snapshots. Rate limit: 25 req/s, burst 50, 8 connections per IP.
- Measured listing age (F0-B): only ~11–13% of Europe listings are < 1 h old,
  ~13–22% are 1–6 h. Defaults stay < 1 h fresh / 1–6 h stale.
- Discord message: item, HQ/NQ, world, price, qty, home median, profit, data
  age, `/li <world> market` line.

### Retainer snipe (requested 2026-11-21)
Input: a retainer name (e.g. "Hamsterie") and a world (default home world).
Output: every item that retainer currently lists on that world (qty, ppu,
HQ/NQ, data age), and for each item the cheapest current listing in the buy
region (world, DC, ppu, qty) with the price gap and profit if bought there.
Universalis and Saddlebag have no "listings by retainer" lookup (confirmed
F0-B); finding them means scanning listings across all marketable items on
that world (bulk endpoint, up to 100 items/request) and filtering by
`retainerName`. Measured: 169 bulk requests per world, ~0.1 s each; at the
25 req/s limit (8 connections/IP) one world takes ~10 s. Results only include listings someone has uploaded, so data
age is shown per row. Optional: save retainers to a watchlist and re-check.
Retainer names are public market data but are never committed to fixtures.

## Privacy & repo hygiene
- Real plugin data and Universalis buyer/retainer names are never committed.
  Fixtures go through `tools/anonymize.py` (names → hashes, amounts scaled).
- Secret scan in two places: local pre-commit hook and GitHub Actions
  (gitleaks + Discord webhook URL pattern, see `scripts/check_patterns.py`). Personal patterns (character
  name, retainer names) live in a **gitignored** local file
  `.secrets-patterns.local`, never in the repo.
- Diagnostics export button: zips logs + version info, excludes webhook and
  personal data.

## Phases

Code is written by Codex agents; GLaDOS designs briefs, reviews, merges.
Each Codex phase ends with: changed files, test result, 5-line summary.

| Phase | Work | Done when |
|---|---|---|
| **F0-A** contracts | pyproject, `models.py`, FastAPI stub routes with response models, `openapi.json` export, `ui/` skeleton + generated TS types, `conftest.py`, pre-commit + CI secret scan, lint/test CI | `pytest` green, `openapi.json` + `ui/src/api/types.ts` generated, CI green |
| **F0-B** data | record/replay fixture layer for Universalis/Saddlebag, frozen fixtures, legacy scripts run on fixtures → `tests/golden/`, `tools/anonymize.py`, research notes (WebSocket, rate limits, seller tax, listing-age distribution) | goldens reproducible offline; research doc with sources; no personal data in repo |
| **F1-A** market engine | clients, cache, rate limiter, presets, volume proof | matches goldens (deliberate legacy bug fixes update goldens with a note) |
| **F1-B** ingest + ledger | plugin readers, schema checks, FIFO P&L | ±1 gil vs CashFlow raw on anonymised fixture; edge-case tests above |
| **F1-C** UI | Scan, Snipes, Positions, Settings screens on generated types + mock | screenshots approved |
| **F2** integration | real API, scheduler, Discord, CLI, diagnostics | end-to-end scan; one test message lands in channel |
| **F3** packaging | PyInstaller onedir, launcher/updater, release workflow | v0.1.0 updates itself to v0.1.1 via the folder-swap flow |
| **F4** rollout | test on İlker's machine, friend install guide (incl. SmartScreen "Run anyway") | friend installed and received an update |

F1-A, F1-B, F1-C run in parallel in separate git worktrees. F1 agents must not
edit shared files (see AGENTS.md); they request changes instead.
