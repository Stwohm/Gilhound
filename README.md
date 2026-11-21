# Gilhound

<img src="assets/icon-256.png" width="96" height="96" alt="Gilhound app icon">

FFXIV market-board flipping assistant for Windows: flip scans, periodic snipe
scan with Discord notifications, and position tracking from plugin data
(CashFlow, AllaganMarket, Allagan Tools, Restocky).

Install: see [docs/INSTALL.md](docs/INSTALL.md). Design notes:
[docs/PLAN.md](docs/PLAN.md).

For scans and position tracking, Gilhound reads public market APIs (Universalis
and Saddlebag Exchange) and files that Dalamud plugins write on your PC.
If you enable the optional Restocky bridge, clicking **Go** can move your
character. Gilhound requests only `/li <world> market`, once per click.
When the bridge is unavailable, **Go** copies the command to your clipboard
for you to paste and run in game. **Copy route** always uses the clipboard.

## License

MIT — see [LICENSE](LICENSE).
