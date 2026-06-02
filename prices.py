"""
prices.py
Provider-agnostic price layer for poe2craft.

Provider order:
  1. PoE2 Scout  (prices_scout.py)  — documented MIT API, history smoothing
  2. poe.ninja   (prices_ninja.py)  — fallback if Scout is unavailable
  3. placeholder (in planner.py)    — clearly-labeled, used only if both fail

Run `python prices.py` once to populate prices_cache.json. The planner reads the
cache via load_prices(); if no cache exists it falls back to labeled placeholders.
"""
from __future__ import annotations
import json, os

CACHE = os.path.join(os.path.dirname(__file__), "prices_cache.json")


def refresh(league: str | None = None) -> dict:
    """Try providers in order; write and return the first success.
    Also attempts to fetch essence prices (best-effort) into the cache.
    """
    errors = []
    meta = None
    try:
        import prices_scout
        meta = prices_scout.fetch_currency_prices(league)
        if meta.get("prices"):
            meta["provider"] = "poe2scout"
            # best-effort essence prices alongside currency
            try:
                ess = prices_scout.fetch_essence_prices(meta.get("league"))
                if ess.get("prices"):
                    meta["essence_prices"] = ess["prices"]
                    meta["essence_category"] = ess.get("category")
            except Exception as e:
                meta["essence_error"] = f"{type(e).__name__}: {e}"
            _write(meta)
            return meta
        errors.append("scout: empty price set")
    except Exception as e:
        errors.append(f"scout: {type(e).__name__}: {e}")
    try:
        import prices_ninja
        meta = prices_ninja.build_cache(league) if league else prices_ninja.build_cache()
        if meta.get("prices"):
            meta["provider"] = "poe.ninja"
            _write(meta); return meta
        errors.append("ninja: empty price set")
    except Exception as e:
        errors.append(f"ninja: {type(e).__name__}: {e}")
    raise RuntimeError("All price providers failed:\n  " + "\n  ".join(errors))


def _write(meta: dict) -> None:
    with open(CACHE, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)


def load_prices() -> dict | None:
    try:
        with open(CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


if __name__ == "__main__":
    try:
        meta = refresh()
    except Exception as e:
        print(e)
        print("\nNo live prices available. The planner will use labeled placeholders.")
        raise SystemExit(1)
    print(f"Provider: {meta['provider']}  |  League: {meta['league']}  ({meta['fetched_utc']})")
    print(f"Unit: {meta['unit']}\n")
    for name, v in sorted(meta["prices"].items(), key=lambda kv: kv[1]):
        print(f"  {name:<20} {v:>12.4f} ex")
    print(f"\nCached {len(meta['prices'])} currencies -> {os.path.basename(CACHE)}")
    if meta.get("essence_prices"):
        print(f"Essence prices: {len(meta['essence_prices'])} essences "
              f"(category '{meta.get('essence_category')}')")
    elif meta.get("essence_error"):
        print(f"Essence prices unavailable: {meta['essence_error']}")
