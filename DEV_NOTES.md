# CraftPath - developer notes

Technical reference for the crafting engine and architecture. User-facing docs are in README.md.

## Architecture
Flask app serving a Python crafting engine as a JSON API; the browser frontend
(templates/forge.html) calls the real solver instead of a JS port.

### Entry points
- app.py              - Flask backend + all API routes
- wsgi.py             - production entry (forces DEPLOY_MODE=public for the hosted optimizer)
- launch_craftpath.py - desktop .exe launcher (PyInstaller); chdir to bundle, opens browser
- Procfile            - web: gunicorn wsgi:app   (for Render)

### Core engine
- solver.py         - MDP value-iteration optimizer. Caches per-state action sets
                      (self._action_cache) so value iteration does not recompute the
                      eligible-mod distribution every pass - about 13x speedup, identical
                      results. Critical for the low-CPU hosted tier.
- putrefaction.py   - Omen of Putrefaction + Bone modeling, lord-forcing odds
- desecrated.py     - desecrated (Well of Souls) mod handling
- methods.py        - crafting method/orb definitions
- weight_prior.py   - flat_uniform vs craft_of_exile_estimate weight sourcing
- essences.py       - essence forced-mod logic
- profit_scanner.py - premium scan + putrefaction scan (local-only / desktop)
- trade_client.py   - live pathofexile.com trade2 client, normalized to Exalted.
                      Runtime session store (set via UI), priority over POESESSID env var.
- prices*.py        - price orchestration -> prices_cache.json

### Two editions (one codebase, mode-switched)
- DEPLOY_MODE=public (wsgi.py): market endpoints return local_only; no cookie field.
  Badge: ONLINE EDITION - OPTIMIZER.
- no DEPLOY_MODE (python app.py / .exe): market features on with user POESESSID.
  Badge: DESKTOP - MARKET LIVE.
- /api/config tells the UI which mode it is in.

### Key API routes
- GET  /api/config            - edition, market availability, download URL
- GET  /api/bases             - available bases
- GET  /api/mods/<base>       - mod pool, prefix/suffix, tagged by source
- POST /api/solve             - optimal plan. 4+ specific mods short-circuit to
                                not_viable_by_slamming BEFORE the expensive solve
                                (prevents worker timeouts on the slow hosted CPU).
- POST /api/set-session       - accept POESESSID from UI (403 when hosted)
- POST /api/price-check/<base>, /api/profit-scan/<base> - market (desktop only)

### Error handling
- Global handler returns clean JSON for /api/ routes on any unhandled exception
  (prevents the "Unexpected token <" frontend crash from HTML error pages).
- Optional Sentry (app.py) activates only if SENTRY_DSN env var is set.

## Data sources & honesty
- Mod pool/groups/ilvl: Path of Building (reliable structure).
- Weights: Craft of Exile estimate (dagger only) else flat_uniform - labeled. See WEIGHTS_HOWTO.md.
- Desecrated per-base pools: verified against PoE2DB.
- Alloys: mechanics documented, odds not modeled (not datamined). See ALLOY_NOTES.md.
- Unsupported (no reliable odds): Distilled Emotions, rune modpools, Runes-of-Aldur rune-craft.

## Known tech debt
- prices.py overwrites bone/omen prices on refresh (wholesale cache overwrite).
  Workaround: merge instead of overwrite. Not yet patched.
