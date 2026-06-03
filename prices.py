"""
prices.py
Provider-agnostic price layer for poe2craft.

Provider order:
  1. PoE2 Scout  (prices_scout.py) ; documented MIT API, history smoothing
  2. poe.ninja   (prices_ninja.py) ; fallback if Scout is unavailable
  3. placeholder (in planner.py)   ; clearly-labeled, used only if both fail

Run `python prices.py` once to populate prices_cache.json. The planner reads the
cache via load_prices(); if no cache exists it falls back to labeled placeholders.
"""
from __future__ import annotations
import json, os

from respath import resource_path, writable_dir
CACHE = os.path.join(writable_dir(), "prices_cache.json")
_SEED_CACHE = resource_path("prices_cache.json")  # bundled read-only seed


def _derive_tiered(prices: dict) -> dict:
    """Derive Greater/Perfect orb prices from base orbs when a provider doesn't
    list them. These are real, fairly stable multiples of the base orb (the
    tiered variants are rarer drops). Multipliers are ESTIMATES, flagged as such
    in the cache via 'tiered_estimated'. Only fills keys that are missing."""
    # rough community multiples vs the base orb (Greater ~6x, Perfect ~25x)
    GREATER, PERFECT = 6.0, 25.0
    pairs = [
        ("Transmutation Orb", "Greater Orb of Transmutation", "Perfect Orb of Transmutation"),
        ("Augmentation Orb",  "Greater Orb of Augmentation",  "Perfect Orb of Augmentation"),
        ("Regal Orb",         "Greater Regal Orb",            "Perfect Regal Orb"),
        ("Exalted Orb",       "Greater Exalted Orb",          "Perfect Exalted Orb"),
    ]
    filled = []
    for base, g, p in pairs:
        b = prices.get(base)
        if b is None:
            continue
        if g not in prices:
            prices[g] = round(b * GREATER, 4); filled.append(g)
        if p not in prices:
            prices[p] = round(b * PERFECT, 4); filled.append(p)
    return prices, filled


def refresh(league: str | None = None) -> dict:
    """Try providers in order; MERGE fresh prices over the existing cache so any
    seeded estimates (Greater/Perfect orbs, essences, bones, omens) survive when
    a live fetch doesn't return them. Also attempts essence prices (best-effort).
    """
    existing = load_prices() or {}
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
            _merge_and_write(existing, meta)
            return meta
        errors.append("scout: empty price set")
    except Exception as e:
        errors.append(f"scout: {type(e).__name__}: {e}")
    try:
        import prices_ninja
        meta = prices_ninja.build_cache(league) if league else prices_ninja.build_cache()
        if meta.get("prices"):
            meta["provider"] = "poe.ninja"
            _merge_and_write(existing, meta); return meta
        errors.append("ninja: empty price set")
    except Exception as e:
        errors.append(f"ninja: {type(e).__name__}: {e}")
    raise RuntimeError("All price providers failed:\n  " + "\n  ".join(errors))


def _seed_essence_estimates() -> dict:
    """Build tiered ESTIMATE essence prices for every essence the game has, so
    the essence method always has a price even when no live/cached value exists.
    Tiered by rank (Lesser/Normal/Greater/Perfect). Flagged as estimate."""
    try:
        ess = json.load(open(resource_path("data", "essences_by_class.json")))
    except Exception:
        return {}
    # rough ex values by rank (community ballpark; replace via live fetch)
    RANK = {"perfect": 25.0, "greater": 8.0, "lesser": 0.5, "normal": 2.0}
    out = {}
    for lst in ess.values():
        for e in lst:
            nm = e["name"]; nl = nm.lower()
            if nl.startswith("perfect"):
                out[nm] = RANK["perfect"]
            elif nl.startswith("greater"):
                out[nm] = RANK["greater"]
            elif nl.startswith("lesser"):
                out[nm] = RANK["lesser"]
            else:
                out[nm] = RANK["normal"]
    return out


def _merge_and_write(existing: dict, meta: dict) -> None:
    """Overlay fresh live prices onto the existing cache so estimates survive.
    Live values WIN for any key they cover; everything else (Greater/Perfect,
    essences, bones, omens) is preserved from the prior cache. Greater/Perfect
    are then re-derived from live base orbs where still missing."""
    merged_prices = dict(existing.get("prices", {}))   # start from old (estimates)
    live = meta.get("prices", {})
    merged_prices.update(live)                          # live base orbs override
    merged_prices, derived = _derive_tiered(merged_prices)
    meta["prices"] = merged_prices
    meta["live_keys"] = sorted(live.keys())
    meta["derived_tiered"] = derived
    # preserve essences from old cache if the live fetch didn't get any
    if not meta.get("essence_prices") and existing.get("essence_prices"):
        meta["essence_prices"] = existing["essence_prices"]
        meta["essence_category"] = existing.get("essence_category")
        meta["essence_source"] = "preserved estimate (live fetch returned none)"
    # last-resort: seed tiered estimates so the essence method always has prices
    if not meta.get("essence_prices"):
        seeded = _seed_essence_estimates()
        if seeded:
            meta["essence_prices"] = seeded
            meta["essence_source"] = "seeded estimate (no live or cached prices)"
    _write(meta)


def _write(meta: dict) -> None:
    with open(CACHE, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)


def load_prices() -> dict | None:
    # Prefer the writable cache (refreshed by running prices.py). If it doesn't
    # exist yet (e.g. first run of the packaged .exe), fall back to the bundled
    # seed prices_cache.json that ships inside the build.
    for path in (CACHE, _SEED_CACHE):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            continue
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
    if meta.get("live_keys"):
        print(f"  Live (from {meta['provider']}): {len(meta['live_keys'])} base currencies")
    if meta.get("derived_tiered"):
        print(f"  Derived Greater/Perfect from base orbs: {len(meta['derived_tiered'])} "
              f"(estimated multipliers; flagged)")
    if meta.get("essence_prices"):
        src = meta.get("essence_source", f"live (category '{meta.get('essence_category')}')")
        print(f"  Essence prices: {len(meta['essence_prices'])} essences [{src}]")
    elif meta.get("essence_error"):
        print(f"  Essence prices unavailable: {meta['essence_error']} (kept prior estimates if any)")
