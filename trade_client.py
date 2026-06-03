"""
trade_client.py
Live sell-price estimates from the official PoE2 trade site (api/trade2).

SECURITY: your POESESSID is your logged-in session. It is read ONLY from the
local environment variable POESESSID on the machine that runs this file. It is
never written to disk, never logged, and never sent anywhere except in the
Cookie header of the request to pathofexile.com. Do not paste it into chats,
commits, or screenshots. To rotate it, log out of pathofexile.com.

Run locally (not in any sandbox; this needs the live site + your cookie):
    setx POESESSID "your_cookie_value"     # Windows, once
    # or per-session:  $env:POESESSID="..."  (PowerShell)
    python trade_client.py

The trade2 flow is two calls, heavily rate-limited (respect Retry-After):
  1. POST /api/trade2/search/poe2/<league>   -> {id, result:[hashes...]}
  2. GET  /api/trade2/fetch/<hashes>?query=<id>  (batches of 10)
Returns listing prices; we report a robust low-percentile as the sale estimate.
"""
from __future__ import annotations
import os, time, json, urllib.request, urllib.error, urllib.parse

TRADE2 = "https://www.pathofexile.com/api/trade2"
UA = "poe2craft/1.0 (personal crafting tool; contact: local user)"


class TradeError(RuntimeError):
    pass


_RUNTIME_SESSION = {"poesessid": None}  # set via UI on desktop; never on hosted


def _session() -> str:
    # priority: cookie set through the app UI (desktop), then env var fallback
    sid = (_RUNTIME_SESSION.get("poesessid") or os.environ.get("POESESSID", "")).strip()
    if not sid:
        raise TradeError(
            "POESESSID not set. Enter it in CraftPath's settings (desktop), or "
            "set the POESESSID environment variable. It stays on your machine and "
            "is never sent anywhere but pathofexile.com.")
    return sid


def set_runtime_session(sid: str):
    """Called by the app when the user enters their cookie in the UI (desktop only)."""
    _RUNTIME_SESSION["poesessid"] = (sid or "").strip() or None


def has_session() -> bool:
    return bool((_RUNTIME_SESSION.get("poesessid") or os.environ.get("POESESSID", "")).strip())


def _req(url: str, *, method="GET", body=None):
    headers = {"User-Agent": UA, "Content-Type": "application/json",
               "Cookie": f"POESESSID={_session()}"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode()), r.headers
    except urllib.error.HTTPError as e:
        if e.code == 429:
            retry = e.headers.get("Retry-After", "10")
            raise TradeError(f"Rate limited; wait {retry}s and retry (the trade "
                             f"API allows only a few requests per several seconds).")
        raise TradeError(f"HTTP {e.code}: {e.read().decode()[:200]}")


def build_query(stat_filter_ids: list[str], category: str | None = None,
                item_level_min: int | None = None) -> dict:
    """Build a trade2 search body.

    stat_filter_ids are trade stat hashes (presence search, 'and' filters).
    `category` is a trade CATEGORY string (e.g. 'weapon.dagger', 'armour.gloves'),
    NOT a base item name; it goes in type_filters.category. Passing a raw base
    name in `type` causes 'Unknown item base type', so we never use `type` here.
    """
    filters = [{"id": sid, "disabled": False} for sid in stat_filter_ids]
    q: dict = {"query": {"status": {"option": "online"},
                         "stats": [{"type": "and", "filters": filters}]},
               "sort": {"price": "asc"}}
    type_filters = {}
    if category:
        type_filters["category"] = {"option": category}
    misc_filters = {}
    if item_level_min is not None:
        misc_filters["ilvl"] = {"min": item_level_min}
    qfilters = {}
    if type_filters:
        qfilters["type_filters"] = {"filters": type_filters}
    if misc_filters:
        qfilters["misc_filters"] = {"filters": misc_filters}
    if qfilters:
        q["query"]["filters"] = qfilters
    return q


# map our base tokens -> trade category strings (PoE2 trade2 'category' option)
BASE_CATEGORY = {
    "dagger":"weapon.dagger","claw":"weapon.claw","wand":"weapon.wand","sceptre":"weapon.sceptre",
    "staff":"weapon.staff","flail":"weapon.flail","spear":"weapon.spear","bow":"weapon.bow",
    "crossbow":"weapon.crossbow",
    "one_hand_sword":"weapon.onesword","one_hand_axe":"weapon.oneaxe","one_hand_mace":"weapon.onemace",
    "two_hand_sword":"weapon.twosword","two_hand_axe":"weapon.twoaxe","two_hand_mace":"weapon.twomace",
    "amulet":"accessory.amulet","ring":"accessory.ring","belt":"accessory.belt",
    "quiver":"armour.quiver","focus":"armour.focus","shield":"armour.shield",
    "body":"armour.chest","boots":"armour.boots","gloves":"armour.gloves","helmet":"armour.helmet",
}

def category_for_base(base_token: str) -> str | None:
    return BASE_CATEGORY.get(base_token.split("_")[0]) or BASE_CATEGORY.get(base_token)


def list_trade_categories():
    """Fetch the valid item-category options the trade2 API actually accepts.
    Prints the category filter's option ids/text so BASE_CATEGORY can be set to
    real values. Needs POESESSID + live network.
    """
    data, _ = _req(f"{TRADE2}/data/filters")
    out = []
    def walk(node):
        if isinstance(node, dict):
            if node.get("id") == "category" and "option" in node:
                for opt in node["option"].get("options", []):
                    out.append((opt.get("id"), opt.get("text")))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(data)
    return out


def debug_search(league: str, stat_filter_ids: list[str], category=None,
                 item_level_min=None):
    """Print the exact request body and the raw API response/error, for
    diagnosing query shape. Returns nothing; prints everything."""
    body = build_query(stat_filter_ids, category, item_level_min)
    print("=== REQUEST BODY ===")
    print(json.dumps(body, indent=2))
    league_enc = urllib.parse.quote(league)
    url = f"{TRADE2}/search/poe2/{league_enc}"
    try:
        resp, _ = _req(url, method="POST", body=body)
        print("=== RESPONSE ===")
        print("total results:", resp.get("total"))
        print("result ids returned:", len(resp.get("result", [])))
        print("query id:", resp.get("id"))
    except TradeError as e:
        print("=== ERROR ===")
        print(e)


def _load_ex_rates():
    """Load currency->exalted conversion from prices_cache.json if present.
    Trade quotes come in many currencies; we normalize all to Exalted so margins
    are comparable to craft cost (which is in Exalted)."""
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(here, "prices_cache.json")
    rates = {"exalted": 1.0, "exalt": 1.0, "ex": 1.0}
    try:
        prices = json.load(open(p)).get("prices", {})
        # map common trade currency short-names to our price keys
        name_map = {
            "divine": "Divine Orb", "chaos": "Chaos Orb", "exalted": "Exalted Orb",
            "regal": "Regal Orb", "aug": "Augmentation Orb", "augmentation": "Augmentation Orb",
            "alch": "Orb of Alchemy", "transmute": "Transmutation Orb", "annul": "Orb of Annulment",
            "vaal": "Vaal Orb",
        }
        for short, full in name_map.items():
            if full in prices and prices[full]:
                rates[short] = float(prices[full])  # value already in exalted terms
    except Exception:
        pass
    return rates


def _to_exalted(amount: float, currency: str, rates: dict):
    c = (currency or "").lower()
    rate = rates.get(c)
    return (amount * rate) if rate is not None else None  # None = unknown currency


def estimate_sell_price(league: str, stat_filter_ids: list[str], *,
                        category=None, item_level_min=None,
                        sample=20, low_percentile=0.25, pause=6.0):
    """Return a robust low-percentile listing price for an item matching the
    given mods, or None if no comps. `category` is a trade category string
    (use category_for_base()); pass None to search across all item types.
    Respects rate limits.
    """
    league_enc = urllib.parse.quote(league)
    body = build_query(stat_filter_ids, category, item_level_min)
    search, _ = _req(f"{TRADE2}/search/poe2/{league_enc}", method="POST", body=body)
    ids = search.get("result", [])[:sample]
    qid = search.get("id")
    if not ids:
        return None
    prices = []
    for i in range(0, len(ids), 10):
        batch = ",".join(ids[i:i+10])
        time.sleep(pause)   # rate-limit courtesy
        fetched, _ = _req(f"{TRADE2}/fetch/{batch}?query={qid}")
        for r in fetched.get("result", []):
            p = (r or {}).get("listing", {}).get("price")
            if p and p.get("amount"):
                prices.append((p["amount"], p.get("currency", "?")))
    if not prices:
        return None
    rates = _load_ex_rates()
    # normalize every listing to exalted; drop ones in unknown currency
    ex_amounts = []
    for amt, cur in prices:
        ex = _to_exalted(amt, cur, rates)
        if ex is not None:
            ex_amounts.append(ex)
    if not ex_amounts:
        # couldn't convert any (unknown currencies); report raw with a flag
        amounts = sorted(a for a, _ in prices)
        idx = max(0, int(len(amounts) * low_percentile) - 1)
        return {"estimate": amounts[idx], "currency": prices[0][1],
                "exalted_equiv": None, "n_comps": len(amounts),
                "note": "could not convert to Exalted (unknown currency); raw amount shown"}
    ex_amounts.sort()
    idx = max(0, int(len(ex_amounts) * low_percentile) - 1)
    return {"estimate": round(ex_amounts[idx], 2), "currency": "exalted",
            "exalted_equiv": round(ex_amounts[idx], 2),
            "n_comps": len(ex_amounts), "low": round(ex_amounts[0], 2),
            "high": round(ex_amounts[-1], 2),
            "note": "low-percentile of online listings, normalized to Exalted; an estimate, not a quote"}


if __name__ == "__main__":
    # smoke test (needs POESESSID + live network on your machine)
    try:
        league = os.environ.get("POE_LEAGUE", "Runes of Aldur")
        # example: search by stat hashes you pull from /api/trade2/data/stats
        demo_ids = os.environ.get("DEMO_STAT_IDS", "").split(",") if os.environ.get("DEMO_STAT_IDS") else []
        if not demo_ids:
            print("Set DEMO_STAT_IDS (comma-separated trade stat hashes) to test a real search.")
        else:
            print(estimate_sell_price(league, demo_ids))
    except TradeError as e:
        print("Trade client not ready:", e)
