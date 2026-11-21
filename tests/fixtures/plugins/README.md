# Anonymized plugin fixtures

Run `python -B tools/anonymize.py plugins` with `GILHOUND_FIXTURE_SALT`
set privately to refresh the fixtures. The default input is the local
XIVLauncher plugin directory. Set `GILHOUND_FIXTURE_SCALE` or pass
`--scale` before `plugins` to use a chosen private multiplier. The
default derives a multiplier from the salt. Use a separate salt for
market and plugin refreshes.

The CashFlow SQLite fixture uses the `.db` suffix because the root
ignore rules exclude `.sqlite`. It contains the three ledger tables
in a newly created database. The tool reads the live database through a
read-only SQLite backup in memory.

Local identities use salted hashes. AllaganMarket and InventoryTools
share a numeric pseudonym for matching IDs. Gil values are scaled and
rounded, item IDs are preserved and timestamps are shifted by a common
offset. Original timestamp formats and units are preserved at the export
boundary. CashFlow's `UnixTime` values use milliseconds. The sale
amounts may differ slightly from recomputing a percentage on the scaled
prices because the recorded amounts and prices are rounded independently.
