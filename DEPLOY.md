# Deploying Exile's Forge (free crafting optimizer)

The crafting optimizer; base pools, the MDP solver, odds/costs, desecration and
putrefaction modeling; is safe to host publicly. The market features
(price-check, profit scanning) are **local-only** because they require each
user's own PoE `POESESSID`, which must never live on a shared server. Deployment
hard-disables them.

## What deploys vs what stays local
- **Hosted (free for everyone):** `/`, `/api/bases`, `/api/mods/<base>`,
  `/api/essences/<class>`, `/api/desecrated/<base>`, `/api/putrefaction/<base>`,
  `/api/prices` (cached), `/api/solve`.
- **Local-only (returns 503 when hosted):** `/api/price-check`,
  `/api/profit-scan`, `/api/profit-putrefaction`. Users run the app on their own
  machine with their own POESESSID to use these.

## One-time: how it's wired
`wsgi.py` sets `DEPLOY_MODE=public` (hard-disables market routes) and
`MARKET_ACCESS_MODE=off` by default. `Procfile` runs gunicorn. `prices_cache.json`
ships with the repo so prices work without any live calls.

## Deploy to Render (easiest free option)
1. Push this folder to a GitHub repo.
2. On render.com: New → Web Service → connect the repo.
3. Settings:
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`
   - (Render reads the `Procfile` automatically, so the start command is optional.)
4. Deploy. You get a public `https://<name>.onrender.com` URL.

## Deploy to Railway / Fly
- **Railway:** New Project → Deploy from repo. It reads the `Procfile`. Done.
- **Fly:** `fly launch` (detects Python), then `fly deploy`. Ensure the start
  command matches the Procfile.

## Run locally in production style
```
pip install -r requirements.txt
gunicorn wsgi:app --bind 0.0.0.0:8000
# open http://localhost:8000  (market features off in this mode)
```

## To run locally WITH market features (your machine only)
Don't use wsgi.py. Use the dev entry point so DEPLOY_MODE isn't forced:
```
$env:POESESSID="your_cookie"      # PowerShell; never share this
$env:POE_LEAGUE="Runes of Aldur"
python app.py                      # banner shows "trade market: LIVE"
```

## Refreshing prices on the host
`prices_cache.json` is a static snapshot. To update it, run `python prices.py`
locally (it pulls Scout/poe.ninja) and redeploy, or add a scheduled job that
runs it. The optimizer works fine with a slightly stale price snapshot.
