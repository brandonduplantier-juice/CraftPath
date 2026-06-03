# CraftPath v1.0.0; first release

A Path of Exile 2 crafting cost & path optimizer for the Runes of Aldur league (patch 0.5).

## What it does
- Cheapest expected crafting path to your target mods, with per-step odds and cost
- Full 0.5 system: currency, essences, omens, desecration, putrefaction (with lord-forcing)
- "Craft it or buy it" budget verdict
- Desktop version adds live market price-check and profit scanning (your own PoE session)

## Two editions
- **Online:** free crafting optimizer, no install, no login. Live at https://craftpath.onrender.com
- **Desktop (download below):** optimizer + live market features, runs locally with your POESESSID like Path of Building

## Install (Desktop)
1. Download CraftPath-Desktop.zip below and unzip
2. Install Python 3.12+ (add to PATH)
3. pip install -r requirements.txt
4. Copy RUN_DESKTOP.bat.template to RUN_DESKTOP.bat, then double-click it
5. Open http://127.0.0.1:5000; enter your POESESSID in the app's settings to enable live market pricing

Your session cookie stays on your machine, like Path of Building.

## Accuracy
Desecrated pools and omen modeling are verified against PoE2DB; solver math matches Monte Carlo. Mod weights for non-dagger bases are flat-uniform estimates (real weights aren't in game files). The app labels its confidence throughout. Alloy crafting mechanics are documented but odds are not yet modeled (per-alloy mods not yet datamined; see ALLOY_NOTES.md).

Not affiliated with GGG. Trade API used for personal, non-commercial use only.
