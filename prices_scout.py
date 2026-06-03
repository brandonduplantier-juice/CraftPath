"""
prices_scout.py
PoE2 Scout price provider; primary source.

API (verified from github.com/poe2scout/poe2scout source, MIT):
  League list:  GET /api/{Realm}/Leagues
                -> [{ Value, ShortName, CurrentLeague, ... }]   (PascalCase aliased)
  Currency:     GET /api/poe2/Leagues/{LeagueName}/Currencies/ByCategory?Category=currency
                -> { Items: [{ ApiId, Text, CurrentPrice, CurrentQuantity,
                               PriceLogs: [{ Price, Time, Quantity } | null] }] }

Prices are denominated in the league BASE currency, which is the Exalted Orb for
PoE2; so CurrentPrice is already in Exalted units (Exalted itself = 1.0).

We resolve the current league at runtime (no hardcoded league string) and, when
PriceLogs are present, use the MEDIAN of recent logged prices instead of the raw
snapshot, so a single volatile listing can't skew a cost estimate in a young league.
"""
from __future__ import annotations
import json, statistics, urllib.parse, urllib.request

API = "https://poe2scout.com/api"
REALM = "poe2"
UA = {"User-Agent": "Mozilla/5.0 (poe2craft scout client)"}

# Map our internal method names -> Scout ApiId / Text. ApiId is the stable key;
# Text is a display fallback. Verified names follow PoE2 currency conventions.
NAME_TO_TEXT = {
    "Transmutation Orb": "Orb of Transmutation",
    "Augmentation Orb": "Orb of Augmentation",
    "Regal Orb": "Regal Orb",
    "Exalted Orb": "Exalted Orb",
    "Orb of Annulment": "Orb of Annulment",
    "Divine Orb": "Divine Orb",
    "Chaos Orb": "Chaos Orb",
}


def _get(url: str):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _is_hardcore(lg: dict) -> bool:
    sn = (lg.get("ShortName") or "").lower()
    val = (lg.get("Value") or "")
    return sn.endswith("hc") or val.startswith("HC ") or val == "Hardcore"


def resolve_league() -> str:
    """Return the current softcore (non-HC, non-SSF) PoE2 league Value.

    Scout flags the live league with IsCurrent=true. Both the softcore and
    hardcore variants carry that flag, so we pick the current NON-hardcore one.
    """
    data = _get(f"{API}/{REALM}/Leagues")
    leagues = data if isinstance(data, list) else data.get("Leagues", data)
    # 1) current AND not hardcore
    for lg in leagues:
        if lg.get("IsCurrent") and not _is_hardcore(lg):
            return lg["Value"]
    # 2) any current league (last resort if naming changes)
    for lg in leagues:
        if lg.get("IsCurrent"):
            return lg["Value"]
    # 3) first non-Standard, else first listed
    for lg in leagues:
        if lg.get("Value") not in ("Standard", "Hardcore"):
            return lg["Value"]
    return leagues[0]["Value"]


def _smoothed_price(item: dict, recent: int = 5) -> float | None:
    """Robust price: median of the most RECENT logs (default 5).

    Using only recent logs tracks a fast-moving young-league price instead of
    being dragged toward stale day-one values, while the median still rejects a
    single volatile listing. Falls back to CurrentPrice if no logs exist.
    PriceLogs are assumed newest-last; we take the tail.
    """
    logs = [pl["Price"] for pl in (item.get("PriceLogs") or [])
            if pl and pl.get("Price")]
    if logs:
        tail = logs[-recent:]
        return float(statistics.median(tail))
    cp = item.get("CurrentPrice")
    return float(cp) if cp is not None else None


def fetch_currency_prices(league: str | None = None) -> dict:
    """Return normalized price meta dict (same schema as the poe.ninja client).

    The currency endpoint paginates (default PerPage=25, max 250) and orders
    differently per league, so a single default page can omit cheap orbs. We
    request the max page size, then for any wanted orb still missing we issue a
    targeted ?Search= lookup as a fallback.
    """
    league = league or resolve_league()
    base = (f"{API}/{REALM}/Leagues/{urllib.parse.quote(league)}"
            f"/Currencies/ByCategory?Category=currency")

    items: list[dict] = []
    page = 1
    while True:
        data = _get(f"{base}&PerPage=250&Page={page}")
        items.extend(data.get("Items", []))
        pages = data.get("Pages", 1) or 1
        if page >= pages:
            break
        page += 1
        if page > 20:               # hard safety stop
            break

    by_text = {it.get("Text"): it for it in items}
    by_apiid = {it.get("ApiId"): it for it in items}

    prices: dict[str, float] = {}
    for method_name, text in NAME_TO_TEXT.items():
        it = by_text.get(text) or by_apiid.get(text)
        if it is None:
            # targeted search fallback for this specific orb
            try:
                sd = _get(f"{base}&PerPage=250&Search={urllib.parse.quote(text)}")
                for cand in sd.get("Items", []):
                    if cand.get("Text") == text or cand.get("ApiId") == text:
                        it = cand
                        break
            except Exception:
                it = None
        if it is None:
            continue
        val = _smoothed_price(it)
        if val is not None:
            prices[method_name] = round(val, 6)
    # Exalted is the base currency -> 1.0 by definition; ensure it's present.
    prices.setdefault("Exalted Orb", 1.0)

    import time
    return {
        "league": league,
        "fetched_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "unit": "Exalted Orb equivalents (Exalted = 1.0)",
        "source": "poe2scout.com /api Currencies/ByCategory (PerPage=250, median of PriceLogs)",
        "fetched_count": len(items),
        "prices": prices,
    }


def list_categories(realm_league_path: str) -> dict:
    """Return {currency_categories: [...], unique_categories: [...]} for a league.
    realm_league_path is e.g. 'poe2/Leagues/Runes of Aldur'.
    """
    url = f"{API}/{REALM}/Items/Categories"
    # Categories is realm-scoped with league in path on some versions; the realm
    # form works across versions. Fall back gracefully.
    try:
        return _get(url)
    except Exception:
        return {}


def _find_category_api_id(cats: dict, *needles: str) -> str | None:
    """Find a currency category whose ApiId or Label matches any needle."""
    for c in cats.get("CurrencyCategories", []):
        hay = f"{c.get('ApiId','')} {c.get('Label','')}".lower()
        if any(n.lower() in hay for n in needles):
            return c.get("ApiId")
    return None


def fetch_category_prices(league: str, category_api_id: str) -> dict:
    """Generic: fetch {Text -> smoothed price} for any currency category."""
    base = (f"{API}/{REALM}/Leagues/{urllib.parse.quote(league)}"
            f"/Currencies/ByCategory?Category={urllib.parse.quote(category_api_id)}")
    items, page = [], 1
    while True:
        data = _get(f"{base}&PerPage=250&Page={page}")
        items.extend(data.get("Items", []))
        pages = data.get("Pages", 1) or 1
        if page >= pages or page > 20:
            break
        page += 1
    out = {}
    for it in items:
        val = _smoothed_price(it)
        if val is not None and it.get("Text"):
            out[it["Text"]] = round(val, 6)
    return out


def fetch_essence_prices(league: str | None = None) -> dict:
    """Return {essence display name -> exalted price} for the current league."""
    league = league or resolve_league()
    cats = list_categories(f"{REALM}/Leagues/{league}")
    cat_id = _find_category_api_id(cats, "essence") or "essence"
    prices = fetch_category_prices(league, cat_id)
    import time
    return {
        "league": league,
        "fetched_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "unit": "Exalted Orb equivalents",
        "category": cat_id,
        "source": "poe2scout.com /api Currencies/ByCategory (essence)",
        "prices": prices,
    }


if __name__ == "__main__":
    meta = fetch_currency_prices()
    print(f"League: {meta['league']}  ({meta['fetched_utc']})")
    print(f"Source: {meta['source']}\n")
    for name in NAME_TO_TEXT:
        v = meta["prices"].get(name)
        print(f"  {name:<20} {('%.4f' % v) if v is not None else 'not listed':>12}")
