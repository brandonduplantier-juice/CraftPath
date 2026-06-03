# poe2craft; PoE2 crafting cost & step optimizer

## Quick start (what's free vs what needs setup)

**Free for everyone, works instantly**; the crafting optimizer. Run it and you
get: the cheapest expected crafting path for any target on any of 51 bases, the
odds and expected cost of each step, desecration + putrefaction modeling, and a
budget verdict (craft it, or just buy it). No account, no keys, no setup beyond
Python.

```
pip install -r requirements.txt
python app.py            # open http://127.0.0.1:5000
```

**Optional, runs on YOUR machine only**; live market features (price-check and
profit scanning). These query the official PoE2 trade API, which needs your own
session. They are OFF until you set your `POESESSID` locally:

```
# PowerShell, in the same window you run app.py from:
$env:POESESSID="your_cookie_value"      # from your browser, never share it
$env:POE_LEAGUE="Runes of Aldur"
python app.py                            # banner will show "trade market: LIVE"
```

Your POESESSID is your logged-in session; it's read only from your local
environment and never transmitted anywhere but to pathofexile.com. Do not paste
it into chats, commits, or a shared server. The market features are intentionally
local-only for this reason; the free optimizer is what's meant to be shared or
deployed.

> Access modes (env var `MARKET_ACCESS_MODE`, default `open`): `open` = market
> features free for all; `gated` = require a key from `market_keys.json`; `off` =
> disabled. The gate exists in case GGG ever approves commercial use, but the
> default is free.

---


Given a base item, a target set of mods, and a budget, this computes the
cheapest expected crafting path, the odds and expected cost of each step, and
walks you through it interactively; updating as you report what actually rolls,
or telling you the item is bricked / over budget and you should just buy it.

## Architecture (data flows top to bottom)

```
PoB ModItem.lua ──> pob_loader.py ──> data/<base>_mods.json   (mod pool + weights)
PoB Essence.lua ──> essences.py                               (essence -> forced mod)
poe2scout API   ──> prices_scout.py ─┐
poe.ninja API   ──> prices_ninja.py ─┼─> prices.py ──> prices_cache.json
                                     │   (provider fallback chain)
                                     ▼
item_state.py + probability.py + methods.py
                                     ▼
                                 solver.py   (MDP, value iteration -> optimal policy)
                                     ▼
                              interactive.py  (UI-agnostic step session)
                                     ▼
                                [ future GUI ]
```

## Modules

- **pob_loader.py**; parses PoB's `ModItem.lua` into structured mods. Resolves
  a base's pool by its TAG set (weapon / one_hand_weapon / dagger / ...), which
  is how the game actually assigns mods. CLI:
  `python pob_loader.py <ModItem.lua> <Bases dir> <ItemType> data/<base>_mods.json`
- **essences.py**; parses `Essence.lua` into essence -> guaranteed-mod maps per
  item class, with tiers (Lesser / normal / Greater / Perfect).
- **prices_scout.py**; primary price source (PoE2 Scout). Auto-resolves the
  current softcore league, paginates currencies, smooths via recent price-log
  median, and discovers the essence category dynamically.
- **prices_ninja.py**; fallback price source (poe.ninja).
- **prices.py**; orchestrator: Scout -> poe.ninja -> labeled placeholders.
  `python prices.py` populates `prices_cache.json` (currencies + essences).
- **solver.py**; the optimizer. Models crafting as a Markov Decision Process
  over states `(rarity, secured wanted mods, junk prefix/suffix counts)` and
  solves for the minimum-expected-cost policy by value iteration. Self-loops
  (Exalt junk then Annul it) are solved algebraically. Essence-forcing is a
  deterministic action. Validated against Monte Carlo (matched to the cent).
- **interactive.py**; UI-agnostic crafting session: `next_step()` recommends
  the action with odds and expected remaining cost; `apply_outcome()` advances
  the true state from what you report. Backs the CLI and a future GUI.
- **item_state.py / probability.py / methods.py / planner.py**; supporting
  state model, per-step probability math, currency definitions, and the earlier
  greedy planner (superseded by solver.py for optimization).
- **gen_all_bases.py**; generates mod pools for all 51 bases (incl. armour
  str/dex/int variants) from PoB data.
- **desecrated.py / data/desecrated_per_base.json**; the verified per-base
  desecrated mod pools (counts + per-lord splits), read straight from PoE2DB
  pages. The accuracy backbone of the abyss/putrefaction modeling.
- **putrefaction.py**; models the dominant 0.5 craft (Omen of Putrefaction +
  Bone → up to 6 unrevealed desecrated mods). `plan_putrefaction(base, affix,
  target_count, lord, slots, ...)` returns hit-probability and expected cost,
  with side-targeting and lord-forcing (Sovereign/Liege/Blackblooded) applied
  from the verified per-base/per-lord data.
- **profit_scanner.py**; premium engine. `scan()` ranks orb-slam candidates
  (now filters non-viable absurd-cost single-target slams); `scan_putrefaction()`
  prices realistic putrefaction templates per base vs live comps. Local-only.
- **trade_client.py**; live PoE2 trade API client (pathofexile.com/api/trade2).
  Reads POESESSID from local env only. Normalizes mixed-currency listings to
  Exalted. Proven working end-to-end.
- **weight_prior.py**; sets flat_uniform weights on non-dagger bases (matching
  real CoE behavior; tier rarity is handled by the solver's ilvl gating, not
  weight). Run it after regenerating pools.
- **wsgi.py / Procfile / DEPLOY.md**; production deployment. wsgi forces
  DEPLOY_MODE=public so a hosted instance serves the free optimizer with market
  features hard-disabled. See DEPLOY.md for Render/Railway/Fly steps.

## How to run

```bash
# 1. Build a mod pool for your base (Dagger is essence-supported & validated)
python pob_loader.py /path/PoB/src/Data/ModItem.lua /path/PoB/src/Data/Bases Dagger data/dagger_mods.json
# 2. Fetch live prices (writes prices_cache.json)
python prices.py
# 3. See the optimizer and the interactive walkthrough
python solver.py
python interactive.py
```

## Validation status (honest, current)

CONFIRMED / VERIFIED
- Mod pool resolution by base tags (dagger: 158 mods, matches game tag model).
- Solver expected-cost math: analytic == 20k-run Monte Carlo to the cent.
- Essence forcing: single-mod dagger craft far cheaper via essence than gamble.
- **Spawn weights resolved.** Real dagger CoE weights are FLAT across tiers
  within a group (e.g. all 8 Dexterity tiers = weight 8); tier rarity comes from
  the solver's item-level gating, NOT weight decay. An earlier "tier_prior" that
  imposed geometric decay was WRONG and was reverted; all non-dagger bases now
  use flat_uniform, matching real CoE behavior. weights_source per base is one of:
  `craft_of_exile_estimate` (dagger, real data), `flat_uniform` (others), and the
  badge reports it honestly.
- **Per-base desecrated data VERIFIED** from PoE2DB pages for all craftable bases:
  bow 8/9, crossbow 7/8, spear 8/9, quarterstaff 6/6, wand 6/8, staff 6/6,
  quiver 3/5, amulet 11/20, ring 7/15, belt 8/12 (prefix/suffix); boots 0/16
  verified, body/gloves/helmet 0/16 anchored to it; dagger & claw have NO
  desecrated mods; sceptre none by rule. Per-lord splits recorded for lord-forcing.
- **Omens modeled:** side-targeting (Sinistral/Dextral Necromancy → prefix/suffix)
  and lord-forcing (Sovereign/Liege/Blackblooded → Ulaman/Amanamu/Kurgal) both
  feed the putrefaction odds. Verified example: Liege-forced Amanamu attack speed
  on a bow = 100% / ~36 ex (matches the meta craft from community videos).
- **Single-mod solver cost FIXED.** Was reporting ~188 billion ex because
  prices_cache.json was missing (every currency fell back to a 1e9 placeholder).
  Built the cache from verified rates + added a "restart with fresh base" solver
  action. Single-mod crafts now ~45-65 ex (sane). The decision the tool exists to
  make works: e.g. bow attack speed = orb-slam ~42 ex vs putrefaction 36 ex/100%.

OPEN / NEEDS YOUR INPUT (cannot be done from the build sandbox)
- **Live prices.** prices_cache.json is a MANUAL SNAPSHOT, not live. The fetch
  tool here can't reach the POE2 Scout live API. Run `python prices.py` locally
  to refresh. Runes of Aldur launched 2026-05-29, so early-league prices are
  volatile; refresh before trusting ABSOLUTE ex costs. RELATIVE method
  comparisons are robust to drift.
- **Between-group CoE weights** for non-dagger bases. Only dagger has real CoE
  data. The rest use flat_uniform (correct within-group; between-group differences
  unknown). To upgrade a base: paste its CoE weight table (same workflow that
  verified the desecrated data), and it becomes craft_of_exile_estimate.
- **Live buy-vs-craft / profit scan / price-check.** Built and proven working
  end-to-end, but require YOUR POESESSID and only run locally (never on the
  hosted instance; DEPLOY_MODE=public hard-disables them).
- **GGG commercial-use ruling.** Email oauth@grindinggear.com yourself re: the
  trade API. Determines whether market features can ever be monetized (gated) or
  stay free. Until then MARKET_ACCESS_MODE defaults to "open" (free for all).

DEPLOYMENT
- Free crafting optimizer is hostable (Render/Railway/Fly; see DEPLOY.md).
  `wsgi.py` forces DEPLOY_MODE=public so market features are off server-side.
  Verified: hosted instance serves the optimizer; market endpoints return 503.

## Notes on accuracy
Costs are estimates, not quotes. Early-league prices are volatile and Scout
exposes more than one Divine figure from different metrics. The tool treats cost
as an estimate. Confidence is labeled per data source throughout (weights_source
badges, desecrated `source` fields, the price cache's own provenance metadata).
