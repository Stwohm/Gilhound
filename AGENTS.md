# AGENTS.md — rules for coding agents in this repo

Read `docs/PLAN.md` first. It is the source of truth for scope and decisions.

## Hard rules
1. **No real data is committed.** Never commit files copied from
   `%APPDATA%\XIVLauncher\pluginConfigs`, real character/retainer/buyer names,
   real gil amounts, or raw Universalis history containing names. Fixtures must
   pass through `tools/anonymize.py`.
2. **No secrets.** Never write a Discord webhook URL, token or key into any
   file in the repo, test, log or fixture. Webhook values come from user
   settings at runtime only and must be redacted in logs.
3. **Shared files are owned by the coordinator.** During parallel phases (F1)
   you must NOT edit: `gilhound/models.py`, `pyproject.toml`, `uv.lock`,
   `ui/package.json`, `ui/package-lock.json`, `tests/conftest.py`,
   `.github/`, `AGENTS.md`, `docs/PLAN.md`. If you need a change there, stop
   and write the request in your final report (what, why, exact diff).
4. Stay inside the files your brief assigns. Do not refactor other areas.
5. All time values internally are UTC epoch seconds. Convert at boundaries.
6. Network calls in tests are forbidden; use the replay fixtures.
7. Python: 3.12+, type hints, `ruff` clean, `pytest` green before you finish.
   UI: TypeScript strict, `npm run build` green.
8. Do not commit. The coordinator reviews and commits.

## Final report format (required)
- Changed files (list)
- Test/build result (command + pass/fail counts)
- 5-line summary
- Requests for shared-file changes (if any)
- Open questions (if any)
