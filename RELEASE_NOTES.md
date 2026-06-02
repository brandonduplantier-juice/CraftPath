# CraftPath v1.0.0 — first release

A Path of Exile 2 crafting cost & path optimizer for the Runes of Aldur league.

## What it does
- Cheapest expected crafting path to your target mods, with per-step odds and cost
- Full 0.5 system: currency, essences, omens, desecration, putrefaction (with lord-forcing)
- "Craft it or buy it" budget verdict
- Desktop version adds live market price-check and profit scanning (your own PoE session)

## Two editions
- **Online:** free crafting optimizer, no install, no login (deploy it yourself or use a hosted link)
- **Desktop (download below):** optimizer + live market features, runs locally with your POESESSID like Path of Building

## Install (Desktop)
1. Download `CraftPath-Desktop.zip` below and unzip
2. Install Python 3.12+ (add to PATH)
3. `pip install -r requirements.txt`
4. Copy `RUN_DESKTOP.bat.template` -> `RUN_DESKTOP.bat`, paste your POESESSID, save
5. Double-click `RUN_DESKTOP.bat`, open http://127.0.0.1:5000

See README for details. Your session cookie stays on your machine.

## Accuracy
Desecrated pools and omen modeling are verified against PoE2DB; solver math matches Monte Carlo. Mod weights for non-dagger bases are flat-uniform estimates (real weights aren't in game files). The app labels its confidence throughout.

Not affiliated with GGG. Trade API used for personal, non-commercial use only.
