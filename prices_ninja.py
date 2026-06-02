"""
prices_ninja.py
Live currency prices from poe.ninja's PoE2 economy API.

Endpoint (verified against the poe2-mcp-server reference client):
  https://poe.ninja/poe2/api/economy/exchange/current/overview?league=<L>&type=<T>
Response: { core: {items:[{id,name,...}], rates:{}, primary, secondary},
            lines: [{id, primaryValue, volumePrimaryValue, ...}] }
Rate limit: ~10 requests / 5 min — so we fetch once and cache to disk.

Values are normalized to Exalted-orb-equivalents (Exalted = 1.0), matching the
unit the planner reports. Run `python prices.py` once to populate the cache;
the planner then reads the cache. If no cache exists and the network is
unavailable, the planner falls back to clearly-labeled placeholder costs.
"""
from __future__ import annotations
import json, time, urllib.parse, urllib.request, os

LEAGUE = "Runes of Aldur"
BASE = "https://poe.ninja/poe2/api/economy"
CACHE = os.path.join(os.path.dirname(__file__), "prices_cache_ninja.json")
UA = {"User-Agent": "Mozilla/5.0 (poe2craft price client)"}

# currency display names the planner asks about
WANTED = ["Transmutation Orb", "Augmentation Orb", "Regal Orb",
          "Exalted Orb", "Orb of Annulment", "Divine Orb", "Chaos Orb"]


def _fetch(type_: str, league: str) -> dict:
    url = (f"{BASE}/exchange/current/overview"
           f"?league={urllib.parse.quote(league)}&type={urllib.parse.quote(type_)}")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def build_cache(league: str = LEAGUE) -> dict:
    """Fetch Currency prices, normalize to Exalted units, write cache."""
    data = _fetch("Currency", league)
    id_to_name = {it["id"]: it["name"] for it in data.get("core", {}).get("items", [])}
    raw = {}                              # name -> primaryValue
    for ln in data.get("lines", []):
        name = id_to_name.get(ln["id"], ln["id"])
        raw[name] = ln.get("primaryValue", 0.0)
    ex_ref = raw.get("Exalted Orb") or 1.0
    table = {name: round(v / ex_ref, 6) for name, v in raw.items()}
    out = {
        "league": league,
        "fetched_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "unit": "Exalted Orb equivalents (Exalted = 1.0)",
        "primary_ref": data.get("core", {}).get("primary"),
        "source": "poe.ninja /poe2/api/economy/exchange/current/overview",
        "prices": table,
    }
    with open(CACHE, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    return out


def load_prices() -> dict | None:
    """Return cached price meta dict, or None if no cache exists."""
    try:
        with open(CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


if __name__ == "__main__":
    try:
        meta = build_cache()
    except Exception as e:
        print(f"Live fetch failed ({type(e).__name__}: {e}).")
        print("Check the league name is current and you have network access.")
        raise SystemExit(1)
    print(f"Prices for '{meta['league']}'  ({meta['fetched_utc']}, "
          f"unit = {meta['unit']})\n")
    p = meta["prices"]
    for name in WANTED:
        v = p.get(name)
        print(f"  {name:<20} {('%.4f' % v) if v is not None else 'not listed':>12}")
    print(f"\nCached {len(p)} currencies -> {os.path.basename(CACHE)}")
