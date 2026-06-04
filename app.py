"""
app.py: Flask backend for poe2craft (Exile's Forge).

Serves the Python crafting engine as a JSON API so the browser frontend calls
the real solver instead of a drifting JS port. Endpoints:

  GET  /                      -> the crafting UI (templates/forge.html)
  GET  /api/bases             -> available bases [{token, label, class_name}]
  GET  /api/mods/<base>       -> mod pool for a base, split prefix/suffix,
                                 each tagged by SOURCE (base/desecrated/essence)
  GET  /api/essences/<class>  -> essences that force a mod on this class
  GET  /api/prices            -> current cached prices (currency + essence)
  POST /api/solve             -> {base,item_level,prefixes[],suffixes[],budget}
                                 returns optimal plan, expected cost, steps

DATA SOURCES (and their honesty status):
  - Mod POOL + groups + ilvl: PoB ModItem.lua (reliable structure).
  - Mod WEIGHTS: Craft of Exile extrapolated weights (NOT in the game client;
    statistical estimate). Loaded from data/coe_weights.json once parsed.
    Until that file exists we fall back to PoB's flat 0/1 weights and the API
    flags weights_source="flat_placeholder" so the UI can warn.
  - Desecrated (Well of Souls) + Essence mods: separate SOURCES with their own
    weights, tagged per-mod. Modeled where data exists; Alloys / Distilled
    Emotions / special rune modpools / Runes of Aldur rune-craft are flagged
    unsupported (no reliable probabilities, per CoE's own changelog).
"""
from __future__ import annotations
import json, os
from flask import Flask, jsonify, request, send_from_directory

import solver as solver_mod
from solver import Solver, State
from essences import parse_essences, essences_for_class

from respath import resource_root, resource_path, writable_dir

HERE = resource_root()
DATA = resource_path("data")

# --- Optional error tracking (Sentry) -------------------------------------
# Only activates if a SENTRY_DSN env var is set (on the hosted instance).
# Local/desktop runs without it are completely unaffected. Captures unhandled
# exceptions with full context so real user-hit bugs surface automatically.
def _init_sentry():
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        sentry_sdk.init(
            dsn=dsn,
            integrations=[FlaskIntegration()],
            traces_sample_rate=0.0,          # errors only, no perf overhead
            send_default_pii=False,          # never send user IP / cookies
            release=os.environ.get("RENDER_GIT_COMMIT", "craftpath@dev"),
        )
    except Exception:
        pass  # tracking must never break the app

_init_sentry()

app = Flask(__name__,
            template_folder=resource_path("templates"),
            static_folder=resource_path("static"))


@app.errorhandler(500)
@app.errorhandler(Exception)
def _handle_unexpected(err):
    # Return clean JSON for API routes so the frontend never tries to parse an
    # HTML error page (that caused the 'Unexpected token <' failures). Sentry,
    # if configured, has already captured the exception with full context.
    from werkzeug.exceptions import HTTPException
    if isinstance(err, HTTPException) and err.code != 500:
        return err  # let normal 404s etc. behave normally
    if request.path.startswith("/api/"):
        return jsonify({
            "error": "Something went wrong on our end.",
            "report": "If this keeps happening, please report it: "
                      "https://github.com/brandonduplantier-juice/CraftPath/issues",
        }), 500
    return err

# ---------------------------------------------------------------------------
# MARKET ACCESS GATE (premium features: price-check + profit scanners)
#
# Why this exists: the market features hit PoE's trade API. GGG's Terms allow
# trade data only for "personal and non-commercial" use, and their API requires
# OAuth (not POESESSID) for any distributed app. So a PAID gate is only lawful
# AFTER (a) GGG approves commercial use via oauth@grindinggear.com, and (b) auth
# is migrated to OAuth on a registered HTTPS domain. Until then this gate is
# STRUCTURE ONLY. No payment processor is wired.
#
# MARKET_ACCESS_MODE (env var, default 'open'):
#   'open'  - everyone gets market features, no key (CURRENT DEFAULT, assumes
#             GGG's non-commercial terms apply, so the tool is free for all)
#   'gated' - requires a valid license key (X-License-Key header or ?key=)
#   'off'   - market features disabled for everyone
#
# If GGG ever approves commercial use: set MARKET_ACCESS_MODE=gated, add keys to
# market_keys.json, migrate auth to OAuth. The gate machinery below stays ready.
# ---------------------------------------------------------------------------
from functools import wraps

MARKET_ACCESS_MODE = os.environ.get("MARKET_ACCESS_MODE", "open").lower()


def _valid_keys():
    p = os.path.join(HERE, "market_keys.json")
    try:
        return set(json.load(open(p)).get("keys", []))
    except Exception:
        return set()


def requires_market_access(fn):
    """Gate a premium (market) endpoint per MARKET_ACCESS_MODE."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        # DEPLOY_MODE=public hard-disables market features regardless of anything
        # else: for hosting the free optimizer where no one's POESESSID belongs.
        if os.environ.get("DEPLOY_MODE", "").lower() == "public":
            return jsonify({
                "error": "Market features are local-only. This hosted instance "
                         "runs the free crafting optimizer; run the app locally "
                         "with your own POESESSID to use price-check / profit tools.",
                "access_mode": "local_only",
            }), 503
        mode = os.environ.get("MARKET_ACCESS_MODE", MARKET_ACCESS_MODE).lower() or "open"
        if mode == "open":
            return fn(*args, **kwargs)
        if mode == "off":
            return jsonify({"error": "Market features are currently disabled.",
                            "access_mode": "off"}), 503
        # gated: require a valid license key
        key = request.headers.get("X-License-Key") or request.args.get("key", "")
        if key and key in _valid_keys():
            return fn(*args, **kwargs)
        return jsonify({
            "error": "Premium feature. Market/profit tools require access.",
            "access_mode": "gated",
            "how": "Provide a valid license key via the X-License-Key header or ?key=. "
                   "Crafting-path optimization remains free.",
        }), 402   # 402 Payment Required
    return wrapper

# ---------------------------------------------------------------------------
# data loading (cached in memory)
# ---------------------------------------------------------------------------
_CACHE = {}

def _load_mod_pool(base: str):
    """Return (mods_list, weights_source). Prefers CoE weights when present."""
    key = f"pool::{base}"
    if key in _CACHE:
        return _CACHE[key]
    path = os.path.join(DATA, f"{base}_mods.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"no mod pool for base '{base}'")
    raw_blob = json.load(open(path))
    raw = raw_blob["mods"]

    # overlay CoE weights if available for this base; otherwise honor the file's
    # own weights_source (tier_prior or flat_placeholder).
    coe_path = os.path.join(DATA, "coe_weights.json")
    wsource = raw_blob.get("weights_source", "flat_placeholder")
    coe = {}
    if os.path.exists(coe_path):
        blob = json.load(open(coe_path))
        coe = blob.get(base, {})
        if coe and isinstance(coe, dict):
            wsource = "craft_of_exile_estimate"

    class M:
        def __init__(s, d):
            s.mod_id = d["mod_id"]
            s.affix_type = d["affix_type"]
            s.group = d["group"]
            s.level = d["level"]
            s.text = d.get("text", [])
            s.weight = d.get("weight", 1)
            s.source = d.get("source", "base")
        def weight_for(s, _): return s.weight
        def weight_for_tags(s, _): return s.weight
    mods = []
    for d in raw:
        m = M(d)
        if m.mod_id in coe:
            m.weight = coe[m.mod_id]
        mods.append(m)
    _CACHE[key] = (mods, wsource)
    return mods, wsource


def _essences_by_class():
    if "ess_map" not in _CACHE:
        p = os.path.join(DATA, "essences_by_class.json")
        _CACHE["ess_map"] = json.load(open(p)) if os.path.exists(p) else {}
    return _CACHE["ess_map"]


# map a base token to its essence class name
def _class_for_token(token: str) -> str:
    ARMOUR = {"body": "Body Armour", "boots": "Boots", "gloves": "Gloves",
              "helmet": "Helmet", "shield": "Shield"}
    for pre, cls in ARMOUR.items():
        if token.startswith(pre + "_") or token == pre:
            return cls
    SIMPLE = {
        "amulet":"Amulet","belt":"Belt","ring":"Ring","quiver":"Quiver","focus":"Focus",
        "claw":"Claw","dagger":"Dagger","flail":"Flail","spear":"Spear","bow":"Bow",
        "crossbow":"Crossbow","staff":"Staff","talisman":"Talisman","wand":"Wand","sceptre":"Sceptre",
        "quarterstaff":"Warstaff",
        "one_hand_axe":"One Hand Axe","one_hand_mace":"One Hand Mace","one_hand_sword":"One Hand Sword",
        "two_hand_axe":"Two Hand Axe","two_hand_mace":"Two Hand Mace","two_hand_sword":"Two Hand Sword",
    }
    return SIMPLE.get(token, token.replace("_", " ").title())


def _prices():
    # writable refresh (from running prices.py) wins; else the bundled seed.
    for p in (os.path.join(writable_dir(), "prices_cache.json"),
              resource_path("prices_cache.json")):
        if os.path.exists(p):
            try:
                return json.load(open(p))
            except Exception:
                continue
    return {"prices": {}, "essence_prices": {}, "league": "unknown",
            "note": "run prices.py to populate"}


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(resource_path("templates"), "forge.html")


@app.route("/api/bases")
def api_bases():
    idx_path = os.path.join(DATA, "bases_index.json")
    if os.path.exists(idx_path):
        idx = json.load(open(idx_path))
        # which bases have CoE weights loaded
        coe_path = os.path.join(DATA, "coe_weights.json")
        coe = json.load(open(coe_path)) if os.path.exists(coe_path) else {}
        for b in idx:
            b["has_coe_weights"] = b["token"] in coe
        return jsonify(sorted(idx, key=lambda b: b["label"]))
    # fallback: scan for *_mods.json
    bases = []
    for fn in os.listdir(DATA):
        if fn.endswith("_mods.json"):
            token = fn[:-len("_mods.json")]
            bases.append({"token": token, "label": token.capitalize()})
    return jsonify(sorted(bases, key=lambda b: b["label"]))


@app.route("/api/item-art")
def api_item_art():
    """Serve harvested base->art-url map (data/item_art.json) if present.
    Populated by running harvest_item_art.py locally. Empty {} otherwise."""
    p = os.path.join(DATA, "item_art.json")
    if os.path.exists(p):
        try:
            return jsonify(json.load(open(p)))
        except Exception:
            pass
    return jsonify({})


@app.route("/api/mods/<base>")
def api_mods(base):
    try:
        mods, wsource = _load_mod_pool(base)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    # Which mods can an ESSENCE force? Build the set of (group -> best essence
    # tier level) this item class's essences can guarantee, so the UI can grey
    # out mods no essence reaches when "Use Essence" is checked.
    item_class = _class_for_token(base)
    md = {m.mod_id: m for m in mods}
    ess_reach = {}   # group -> highest forced mod level available via essence
    ess_tier_by_group = {}   # group -> set of tiers ('normal'/'greater') available
    for e in _essences_by_class().get(item_class, []):
        fm = e.get("mod")
        if fm in md:
            g = md[fm].group
            lvl = md[fm].level
            ess_reach[g] = max(ess_reach.get(g, 0), lvl)
            nm = e["name"].lower()
            tier = "greater" if nm.startswith("greater") else (
                   "perfect" if nm.startswith("perfect") else "normal")
            ess_tier_by_group.setdefault(g, set()).add(tier)
    def row(m):
        # essence can hit this mod if an essence forces its group at a tier
        # whose level >= this mod's level (a lower-tier essence can't reach it)
        ess_ok = m.group in ess_reach and ess_reach[m.group] >= m.level
        return {"id": m.mod_id, "type": m.affix_type, "group": m.group,
                "level": m.level, "weight": m.weight,
                "text": (m.text[0] if m.text else m.mod_id),
                "source": getattr(m, "source", "base"),
                "essence_ok": ess_ok,
                "essence_tiers": sorted(ess_tier_by_group.get(m.group, set())) if ess_ok else []}
    pre = [row(m) for m in mods if m.affix_type == "Prefix"]
    suf = [row(m) for m in mods if m.affix_type == "Suffix"]
    return jsonify({"base": base, "weights_source": wsource,
                    "tier_floor": {"greater": 44, "perfect": 50},
                    "prefixes": sorted(pre, key=lambda r: r["text"]),
                    "suffixes": sorted(suf, key=lambda r: r["text"])})


@app.route("/api/essences/<cls>")
def api_essences(cls):
    return jsonify(_essences_by_class().get(cls, []))


def _desecrated_all():
    if "desec" not in _CACHE:
        p = os.path.join(DATA, "desecrated_mods.json")
        _CACHE["desec"] = json.load(open(p)) if os.path.exists(p) else {"mods": []}
    return _CACHE["desec"]


@app.route("/api/desecrated/<base>")
def api_desecrated(base):
    import desecrated as D
    from build_weights import weight_for_base
    blob = _desecrated_all()
    bt = base.split("_")[0]
    if bt in D.NO_DESECRATED:
        return jsonify({"base": base, "available": False,
                        "reason": "This base has no exclusive desecrated modifiers.",
                        "prefixes": [], "suffixes": []})
    pre, suf = [], []
    for m in blob.get("mods", []):
        if not D.can_roll_desecrated(base, m["affix_type"]):
            continue
        # NEW: filter by real per-base weight tags from PoB ModVeiled. A mod is
        # only shown if it can actually roll on THIS base (weight > 0). Mods
        # without tag data (older entries) fall through as shown, to be safe.
        tags = m.get("tags")
        if tags:
            w = weight_for_base(tags, base)
            if w is None or w <= 0:
                continue
        row = {"id": m["mod_id"], "type": m["affix_type"], "lord": m["lord"],
               "level": m.get("ilvl", 65), "text": m["text"], "source": "desecrated"}
        (pre if m["affix_type"] == "Prefix" else suf).append(row)
    return jsonify({
        "base": base, "available": bool(pre or suf),
        "lord_omens_valid": D.lord_omen_valid(base),
        "prefix_note": ("Body/Gloves/Boots/Helmet have no prefix desecrated mods."
                        if bt in D.NO_PREFIX_DESECRATED else None),
        "weights_note": "Filtered to mods that can roll on this base (PoB data). Reveal odds among them are unpublished; shown flat.",
        "prefixes": pre, "suffixes": suf})


@app.route("/api/set-session", methods=["POST"])
def api_set_session():
    """Accept the user's POESESSID from the UI (desktop edition only).
    HARD-BLOCKED when hosted: a public server must never receive cookies."""
    if os.environ.get("DEPLOY_MODE", "").lower() == "public":
        return jsonify({"ok": False, "error": "Disabled on the hosted version. "
                        "Market features run locally only; download CraftPath Desktop."}), 403
    import trade_client as TC
    body = request.get_json(silent=True) or {}
    sid = (body.get("poesessid") or "").strip()
    TC.set_runtime_session(sid)
    return jsonify({"ok": True, "market_live": bool(sid)})


@app.route("/api/config")
def api_config():
    """Tells the UI which version it's running so it can present itself correctly."""
    is_hosted = os.environ.get("DEPLOY_MODE", "").lower() == "public"
    return jsonify({
        "edition": "online" if is_hosted else "desktop",
        "market_available": not is_hosted,
        "market_live": (not is_hosted) and __import__("trade_client").has_session(),
        "download_url": "https://github.com/brandonduplantier-juice/CraftPath/releases",
        # league used to build official trade2 prefilled-search links. Leagues
        # rotate, so this is env-overridable without a code change.
        "trade_league": os.environ.get("TRADE_LEAGUE", "Runes of Aldur"),
    })


@app.route("/api/prices")
def api_prices():
    return jsonify(_prices())


@app.route("/api/build-guides")
def api_build_guides():
    """Curated, hand-authored crafting recipes for common gear (class -> slot ->
    budget). These are guides, not solver output, because they use deterministic
    recipes (rune-forging, fracture-locks, desecration) outside the optimizer's
    scope. Costs are flagged volatile estimates."""
    try:
        with open(resource_path("data", "build_guides.json"), encoding="utf-8") as fh:
            return jsonify(json.load(fh))
    except Exception as e:
        return jsonify({"error": str(e), "classes": {}, "shared_slots": {}}), 200


@app.route("/api/putrefaction/<base>")
def api_putrefaction(base):
    """Estimate putrefaction (Omen of Putrefaction + Bone) odds for a base.
    Query: ?affix=Suffix&lord=amanamu&targets=1&attempt_cost=36
    """
    import putrefaction as PF
    affix = request.args.get("affix", "Suffix")
    lord = request.args.get("lord") or None
    targets = int(request.args.get("targets", 1))
    cost = float(request.args.get("attempt_cost", 36.0))
    plan = PF.plan_putrefaction(base, affix, target_count=targets, lord=lord,
                                attempt_cost=cost)
    if plan is None:
        return jsonify({"base": base, "available": False,
                        "reason": "Putrefaction not applicable (slot rule or no pool)."})
    return jsonify({"base": base, "available": True, **vars(plan)})


@app.route("/api/profit-scan/<base>")
@requires_market_access
def api_profit_scan(base):
    """PREMIUM: rank candidate crafts for a base by expected profit margin.
    Cost side is computed offline by the solver. Sell side needs the trade API
    (user credentials); until wired, rows are ranked cost-only and flagged.
    """
    import profit_scanner as PS
    try:
        mods, wsource = _load_mod_pool(base)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    pdata = _prices()
    prices = pdata.get("prices", {})
    ess_prices = pdata.get("essence_prices", {})
    item_class = _class_for_token(base)
    ess_raw = _essences_by_class().get(item_class, [])
    class E:
        def __init__(s, d): s.name=d["name"]; s._mod=d.get("mod")
        def forced_mod(s, _c): return s._mod
    essences = [E(d) for d in ess_raw] if ess_raw else None
    strategies = tuple(request.args.get("strategies", "heuristic").split(","))
    # market provider auto-enables only if POESESSID is set on THIS machine
    market = PS.MarketPriceProvider(pdata.get("league", "?"),
                                    enable=__import__("trade_client").has_session())
    rows = PS.scan(base, strategies=strategies, prices=prices, essences=essences,
                   item_class=item_class, essence_prices=ess_prices, market=market,
                   mods=mods)
    return jsonify({
        "base": base, "weights_source": wsource,
        "market_wired": market.available,
        "note": ("Sell prices require the PoE2 trade API (your credentials); "
                 "until wired, crafts are ranked by expected cost only. "
                 "Profit estimates are never guarantees; a player economy "
                 "arbitrages reliable margins away and early-league prices swing."),
        "rows": [vars(r) for r in rows],
    })


@app.route("/api/profit-putrefaction/<base>")
@requires_market_access
def api_profit_putrefaction(base):
    """PREMIUM: realistic putrefaction profit craft: multi-stat templates,
    costed at ~36 ex/attempt over hit-probability, priced vs live comps."""
    import profit_scanner as PS, json as _json, os as _os
    pdata = _prices()
    league = pdata.get("league", "Runes of Aldur")
    market = PS.MarketPriceProvider(league, enable=__import__("trade_client").has_session())
    sp = os.path.join(DATA, "trade_stat_ids.json")
    stat_ids = _json.load(open(sp)) if os.path.exists(sp) else {}
    attempt_cost = float(request.args.get("attempt_cost", 36.0))
    rows = PS.scan_putrefaction(base, market=market, attempt_cost=attempt_cost,
                                stat_ids=stat_ids, league=league)
    return jsonify({
        "base": base, "market_wired": market.available,
        "method": "Omen of Putrefaction + Bone (~%g ex/attempt)" % attempt_cost,
        "note": ("Realistic multi-stat craft. Odds are CONSERVATIVE (global "
                 "desecrated pool, not yet per-base filtered) so real hit-rates "
                 "and margins are better than shown. Estimates, not guarantees."),
        "rows": [vars(r) for r in rows],
    })


@app.route("/api/price-check/<base>", methods=["POST"])
@requires_market_access
def api_price_check(base):
    """ACCURATE price lookup. The caller supplies the ACTUAL mods on a finished
    item (the ones they hit), and gets the live market price for that exact spec.
    No guessing of combos; this sidesteps the profit-scanner's accuracy gaps by
    letting you price what you really have.

    POST JSON: {
      "mod_ids": ["MovementVelocity3", "IncreasedLife9", ...],   # required
      "min_values": {"MovementVelocity3": 30, ...},               # optional, per mod
      "item_level_min": 80                                        # optional
    }
    Returns the normalized-to-Exalted low-percentile listing price + spread.
    """
    if not __import__("trade_client").has_session():
        return jsonify({"base": base, "market_wired": False,
                        "error": "Set POESESSID locally to enable live price lookup."}), 400
    body = request.get_json(silent=True) or {}
    mod_ids = body.get("mod_ids", [])
    if not mod_ids:
        return jsonify({"error": "provide mod_ids (a list of mod identifiers)"}), 400
    min_values = body.get("min_values", {})
    ilvl_min = body.get("item_level_min")

    import json as _json, trade_client as TC
    sp = os.path.join(DATA, "trade_stat_ids.json")
    stat_ids = _json.load(open(sp)) if os.path.exists(sp) else {}

    # resolve mod_ids -> trade stat hashes; build value-bound filters where asked
    resolved, missing, filters = [], [], []
    for mid in mod_ids:
        sid = stat_ids.get(mid)
        if not sid:
            missing.append(mid); continue
        resolved.append(mid)
        f = {"id": sid, "disabled": False}
        if mid in min_values:
            f["value"] = {"min": float(min_values[mid])}
        filters.append(f)
    if not filters:
        return jsonify({"base": base, "error": "none of the mod_ids mapped to trade stats",
                        "unmapped": missing}), 404

    league = _prices().get("league", "Runes of Aldur")
    cat = TC.category_for_base(base)
    # build a query with explicit value filters (more accurate than presence-only)
    q = {"query": {"status": {"option": "online"},
                   "stats": [{"type": "and", "filters": filters}]},
         "sort": {"price": "asc"}}
    qf = {}
    if cat:
        qf["type_filters"] = {"filters": {"category": {"option": cat}}}
    if ilvl_min is not None:
        qf["misc_filters"] = {"filters": {"ilvl": {"min": ilvl_min}}}
    if qf:
        q["query"]["filters"] = qf

    try:
        import urllib.parse
        league_enc = urllib.parse.quote(league)
        search, _ = TC._req(f"{TC.TRADE2}/search/poe2/{league_enc}", method="POST", body=q)
        ids = search.get("result", [])[:20]
        total = search.get("total", 0)
        if not ids:
            return jsonify({"base": base, "market_wired": True, "n_comps": 0,
                            "matched_mods": resolved, "unmapped": missing,
                            "total_listings": total,
                            "note": "no live listings match that exact spec; loosen "
                                    "min_values or drop a mod to find comps."})
        # fetch + normalize prices
        import time
        rates = TC._load_ex_rates()
        ex = []
        qid = search.get("id")
        for i in range(0, len(ids), 10):
            time.sleep(6.0)
            fetched, _ = TC._req(f"{TC.TRADE2}/fetch/{','.join(ids[i:i+10])}?query={qid}")
            for r in fetched.get("result", []):
                p = (r or {}).get("listing", {}).get("price")
                if p and p.get("amount"):
                    v = TC._to_exalted(p["amount"], p.get("currency", ""), rates)
                    if v is not None:
                        ex.append(v)
        if not ex:
            return jsonify({"base": base, "market_wired": True, "n_comps": 0,
                            "note": "listings found but prices in unconvertible currency."})
        ex.sort()
        lo = ex[max(0, int(len(ex) * 0.25) - 1)]
        return jsonify({
            "base": base, "market_wired": True, "currency": "exalted",
            "price_estimate": round(lo, 2), "low": round(ex[0], 2),
            "high": round(ex[-1], 2), "n_comps": len(ex), "total_listings": total,
            "matched_mods": resolved, "unmapped": missing,
            "note": "low-percentile of online listings matching your spec, "
                    "normalized to Exalted. An estimate from real comps, not a quote."})
    except TC.TradeError as e:
        return jsonify({"base": base, "market_wired": True, "error": str(e)}), 502


@app.route("/api/parse-item", methods=["POST"])
def api_parse_item():
    """Parse a pasted PoE item into a starting-item spec. Auto-detects the base
    type from the paste, then matches mod lines to that base's pool, infers
    prefix/suffix, and flags unmatched lines."""
    body = request.get_json(force=True)
    base = body.get("base", "")
    raw = body.get("text", "")
    import item_parser
    # auto-detect the base from the pasted text; fall back to the sent base
    _idx_path = os.path.join(DATA, "bases_index.json")
    valid = set()
    if os.path.exists(_idx_path):
        valid = {b["token"] for b in json.load(open(_idx_path))}
    detected = item_parser.detect_base(raw, valid)
    # known-but-unsupported base (e.g. quarterstaff has no mod pool yet)
    if detected and detected.startswith("__unsupported__"):
        cls = detected.replace("__unsupported__", "")
        try:
            import datastore
            datastore.record_paste(cls, 0, 0, [], unsupported_base=cls)
        except Exception:
            pass
        return jsonify({"ok": False,
            "error": f"{cls.capitalize()} isn't in CraftPath's mod pool yet, so it "
                     f"can't be parsed accurately. Supported: armour pieces, "
                     f"jewellery, and most weapons.",
            "unsupported_base": cls}), 200
    used_base = detected or base
    try:
        mods, _ = _load_mod_pool(used_base)
    except FileNotFoundError as e:
        return jsonify({"ok": False, "error": str(e)}), 404
    pool = [{"mod_id": m.mod_id, "affix_type": m.affix_type,
             "group": getattr(m, "group", None),
             "text": m.text} for m in mods]
    result = item_parser.parse_item(raw, pool, base_token=used_base)
    result["detected_base"] = detected          # token or None
    result["used_base"] = used_base
    # Log unmatched mod lines to Sentry (if configured) so the dev sees real
    # text that failed to match and can extend the data files. Low severity.
    if result.get("unmatched"):
        try:
            import sentry_sdk
            sentry_sdk.capture_message(
                "CraftPath unmatched mod lines (base=%s): %s"
                % (used_base, " | ".join(result["unmatched"][:20])),
                level="info")
        except Exception:
            pass
    # Record aggregate, privacy-safe data for refining the mod pool over time.
    try:
        import datastore
        n_matched = result.get("n_matched", 0)
        n_unmatched = result.get("n_unmatched", 0)
        datastore.record_paste(used_base, n_matched + n_unmatched, n_matched,
                               result.get("unmatched", []))
    except Exception:
        pass
    return jsonify(result)


@app.route("/api/data-summary")
def api_data_summary():
    """Refinement dashboard: most-requested missing mods, unsupported bases,
    and match-rate health over time. Read-only, aggregate, privacy-safe."""
    import datastore
    return jsonify(datastore.summary())


@app.route("/data")
def data_dashboard():
    """Visual refinement dashboard: renders the collected aggregate data."""
    return send_from_directory(resource_path("templates"), "data.html")


@app.route("/api/solve", methods=["POST"])
def api_solve():
    body = request.get_json(force=True)
    base = body.get("base", "dagger")
    ilvl = int(body.get("item_level", 81))
    prefixes = body.get("prefixes", [])
    suffixes = body.get("suffixes", [])
    budget = body.get("budget")
    wanted = list(prefixes) + list(suffixes)
    # enabled methods from the Step-2 checkboxes. Empty/missing -> all methods on.
    # Keys: 'essence', 'tiered', 'omens'. Basic orbs always allowed.
    enabled_methods = body.get("methods") or None
    if enabled_methods is not None:
        enabled_methods = [m for m in enabled_methods if m in ("essence", "tiered", "omens")]
        enabled_methods = enabled_methods or None

    # hard validation: PoE2 rare caps at 3 prefixes / 3 suffixes
    if len(prefixes) > 3 or len(suffixes) > 3:
        return jsonify({"status": "invalid",
                        "msg": f"A rare allows max 3 prefixes and 3 suffixes; "
                               f"you asked for {len(prefixes)}P / {len(suffixes)}S."}), 400
    if not wanted:
        return jsonify({"status": "invalid", "msg": "No target mods specified."}), 400

    # --- Starting item state (crafting from gear that already has mods) ---
    # The user can describe their current item: its rarity, which of the TARGET
    # mods are already on it, and how many JUNK (unwanted) prefixes/suffixes
    # occupy slots. Honesty note: the solver tracks junk by COUNT, not identity
    # (see solver.py docstring), so we accept counts, not specific junk mods.
    start_rarity = (body.get("start_rarity") or "Normal").capitalize()
    have_pre = [m for m in body.get("have_prefixes", []) if m in prefixes]
    have_suf = [m for m in body.get("have_suffixes", []) if m in suffixes]
    junk_pre = int(body.get("junk_prefixes", 0) or 0)
    junk_suf = int(body.get("junk_suffixes", 0) or 0)

    if start_rarity not in ("Normal", "Magic", "Rare"):
        return jsonify({"status": "invalid",
                        "msg": "Starting rarity must be Normal, Magic, or Rare."}), 400
    # The starting rarity is just the item's CURRENT state, not a cap on the
    # target: the solver upgrades Normal->Magic->Rare as it crafts. So if the
    # described starting mods imply a higher rarity than stated (e.g. a paste
    # had mods but no Rarity line), upgrade the starting rarity to fit rather
    # than rejecting. Only a true over-cap (more than Rare allows) is invalid.
    tot_pre = len(have_pre) + junk_pre
    tot_suf = len(have_suf) + junk_suf
    if tot_pre > 3 or tot_suf > 3:
        return jsonify({"status": "invalid",
                        "msg": f"An item can hold at most 3 prefixes and 3 suffixes; "
                               f"your starting item describes {tot_pre} prefix / "
                               f"{tot_suf} suffix. Re-check the kept/removed mods."}), 400
    # auto-promote starting rarity to the minimum that fits the described mods
    n_mods = tot_pre + tot_suf
    if n_mods == 0:
        start_rarity = "Normal"
    elif tot_pre <= 1 and tot_suf <= 1 and n_mods <= 2 and start_rarity != "Rare":
        # fits Magic; respect an explicit Rare if the user/paste said so
        start_rarity = "Magic" if start_rarity == "Normal" else start_rarity
    else:
        start_rarity = "Rare"

    # Early viability gate: targeting 4+ NEW specific mods by random orb-slamming
    # is astronomically expensive (which is why putrefaction/desecration exist).
    # Mods already on the item (kept) don't count; they're secured, not slammed.
    already = set(have_pre) | set(have_suf)
    to_acquire = [w for w in wanted if w not in already]
    if len(to_acquire) >= 4:
        return jsonify({
            "not_viable_by_slamming": True,
            "msg": "Targeting this many new specific mods by orb-slamming isn't "
                   "cost-viable (expected cost is astronomical). This is why "
                   "desecration exists - check the desecration plan for this "
                   "base, which adds desecrated mods from the Well of Souls."})

    try:
        mods, wsource = _load_mod_pool(base)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404

    prices = _prices().get("prices", {})
    ess_prices = _prices().get("essence_prices", {})
    item_class = _class_for_token(base)
    ess_raw = _essences_by_class().get(item_class, [])
    class E:
        def __init__(s, d): s.name=d["name"]; s.lvl=d.get("lvl",0); s._mod=d.get("mod")
        def forced_mod(s, _cls): return s._mod
    essences = [E(d) for d in ess_raw] if ess_raw else None

    # --- Desecration wiring: build the base-legal desecrated pool + omen/bone
    # costs from live prices, so the solver can offer Bone+Omen desecration as a
    # crafting step. Reveal weights are unpublished -> modeled flat and flagged.
    desecrated_pool = []
    bone_cost = None
    omen_cost = None
    try:
        import desecrated as D
        from build_weights import weight_for_base
        bt = base.split("_")[0]
        if bt not in D.NO_DESECRATED:
            blob = _desecrated_all()
            for m in blob.get("mods", []):
                if not D.can_roll_desecrated(base, m["affix_type"]):
                    continue
                tags = m.get("tags")
                if tags:
                    w = weight_for_base(tags, base)
                    if w is None or w <= 0:
                        continue
                if True:
                    desecrated_pool.append({
                        "mod_id": m["mod_id"], "affix_type": m["affix_type"],
                        "lord": m.get("lord"), "text": m.get("text", ""),
                        "ilvl": m.get("ilvl", 65)})
            # cheapest bone (any jawbone/collarbone) and cheapest sinistral/dextral omen
            bones = [v for k, v in prices.items()
                     if "jawbone" in k.lower() or "collarbone" in k.lower()]
            omens = [v for k, v in prices.items()
                     if "necromancy" in k.lower()]   # Sinistral/Dextral Necromancy
            bone_cost = min(bones) if bones else None
            omen_cost = min(omens) if omens else None
    except Exception:
        desecrated_pool = []

    # Sinistral/Dextral Exaltation omen cost (steers next Exalt to one side).
    # Use the real price if present; otherwise a flagged placeholder so the
    # method is still offered (Ritual-only omen, often not on currency market).
    _exalt_omen = None
    _exalt_omen_estimated = False
    for k, v in prices.items():
        if "exaltation" in k.lower() and "greater" not in k.lower():
            _exalt_omen = v if _exalt_omen is None else min(_exalt_omen, v)
    if _exalt_omen is None:
        _exalt_omen = 10.0   # placeholder (Ritual omen, price varies)
        _exalt_omen_estimated = True
    # annul omens (Sinistral/Dextral Annulment): real price if present
    _annul_omen = None
    for k, v in prices.items():
        if "annulment" in k.lower() and "omen" in k.lower():
            _annul_omen = v if _annul_omen is None else min(_annul_omen, v)
    # Coronation (steered Regal) and Erasure (steered Chaos removal) omens.
    # Real price if listed, else a flagged placeholder so the method is offered.
    _coronation_omen = None
    for k, v in prices.items():
        if "coronation" in k.lower():
            _coronation_omen = v if _coronation_omen is None else min(_coronation_omen, v)
    if _coronation_omen is None:
        _coronation_omen = 5.0   # placeholder (Ritual omen, price varies)
    _erasure_omen = None
    for k, v in prices.items():
        if "erasure" in k.lower():
            _erasure_omen = v if _erasure_omen is None else min(_erasure_omen, v)
    if _erasure_omen is None:
        _erasure_omen = 5.0      # placeholder (Ritual omen, price varies)
    # Omen of Greater Exaltation (next Exalt adds TWO mods). Real price if listed,
    # else a flagged placeholder. Widely used in 0.5 because it is usually cheaper
    # than two Greater/Perfect Exalts.
    _greater_exalt_omen = None
    for k, v in prices.items():
        if "greater exaltation" in k.lower():
            _greater_exalt_omen = v if _greater_exalt_omen is None else min(_greater_exalt_omen, v)
    if _greater_exalt_omen is None:
        _greater_exalt_omen = 8.0   # placeholder (Ritual omen, price varies)

    sv = Solver(mods, base, ilvl, wanted, prices,
                essences=essences, item_class=item_class, essence_prices=ess_prices,
                desecrated=desecrated_pool or None,
                bone_cost=bone_cost, sinistral_omen_cost=omen_cost,
                exalt_omen_cost=_exalt_omen, annul_omen_cost=_annul_omen,
                coronation_omen_cost=_coronation_omen, erasure_omen_cost=_erasure_omen,
                greater_exalt_omen_cost=_greater_exalt_omen,
                enabled_methods=enabled_methods)
    start = State(start_rarity,
                  frozenset(have_pre + have_suf),
                  junk_pre, junk_suf)
    E_, pol = sv.solve(start)
    total = E_[start]
    solve_approx = not getattr(sv, "converged", True)
    # safety: expected cost can never be negative (all actions cost > 0). A
    # negative value would indicate a numerical degeneracy; treat as unreachable.
    if total < 0:
        total = float("inf")

    # Viability ceiling: targeting 3+ specific mods by random orb-slamming is
    # genuinely astronomically expensive in PoE (which is exactly why putrefaction
    # exists). Rather than print a misleading billions-of-ex figure, flag it as
    # not-viable-by-slamming and point to the realistic method.
    VIABLE_CEILING = 100000.0  # ex
    not_viable = total != float("inf") and total > VIABLE_CEILING

    by_id = {m.mod_id: m for m in mods}
    # desecrated mods aren't in the regular pool; build a text lookup for them
    # so step output can render their names. (key -> plain text string)
    desec_text = {d["mod_id"]: d.get("text", d["mod_id"]) for d in (desecrated_pool or [])}
    def _mod_text(mid):
        if mid in by_id:
            t = by_id[mid].text
            return t[0] if isinstance(t, (list, tuple)) and t else (t if isinstance(t, str) else mid)
        return desec_text.get(mid, mid)
    result = {"status": "ok", "base": base, "item_level": ilvl,
              "weights_source": wsource,
              "expected_cost": (None if (total == float("inf") or not_viable) else round(total, 2)),
              "bricked": total == float("inf"),
              "not_viable_by_slamming": not_viable,
              "over_budget": (budget is not None and total != float("inf")
                              and not not_viable and total > float(budget)),
              "budget": budget, "approximate": solve_approx, "steps": []}
    if total == float("inf") or not_viable:
        # define the putrefaction helper early so both gates can use it
        def _puf_early():
            try:
                wd = [d for d in (desecrated_pool or []) if d["mod_id"] in wanted]
                if not wd:
                    return None
                import putrefaction as PF
                import desecrated as D
                lord_ok = D.lord_omen_valid(base)  # lord omens: weapon/jewellery only
                bt_local = base.split("_")[0]
                # bone + label for THIS base category
                if bt_local in {"amulet", "ring", "belt", "talisman"}:
                    bone, bone_kind, base_kind = "Collarbone", "jewellery", "jewellery"
                elif bt_local in {"quiver", "focus", "shield"}:
                    bone, bone_kind, base_kind = ("Jawbone" if bt_local == "quiver" else "Rib",
                                                  "off-hand", "off-hand")
                elif "body" in base or "boots" in base or "gloves" in base or "helmet" in base:
                    bone, bone_kind, base_kind = "Rib", "armour", "armour"
                else:
                    bone, bone_kind, base_kind = "Jawbone", "weapon", "weapon"
                try:
                    _idx = json.load(open(os.path.join(DATA, "bases_index.json")))
                    base_label = next((b["label"] for b in _idx if b["token"] == base),
                                      base.replace("_", " ").title())
                except Exception:
                    base_label = base.replace("_", " ").title()

                # specific essence for a NON-desecrated wanted mod (so step names the
                # exact essence, not an example). Pick the cheapest essence whose
                # forced mod is one of the wanted regular mods.
                desec_ids = {d["mod_id"] for d in wd}
                regular_wanted = [w for w in wanted if w not in desec_ids]
                essence_step = None
                if essences:
                    best = None  # (price, essence_name, mod_text)
                    for e in essences:
                        if e._mod in regular_wanted:
                            price = ess_prices.get(e.name, 1e9)
                            txt = by_id[e._mod].text[0] if e._mod in by_id else e._mod
                            if best is None or price < best[0]:
                                best = (price, e.name, txt)
                    if best:
                        essence_step = (f"ESSENCE (one only): apply <b>{best[1]}</b> to guarantee "
                                        f"your <b>{best[2]}</b>. Under 0.5 you get ONE essence, so "
                                        f"this is the mod to lock in, since desecration can't target it.")

                recs = []
                shopping = {}   # item -> approx qty (string)
                shopping[f"Abyssal {bone} (bone)"] = "1 per attempt"
                for side in ("Prefix", "Suffix"):
                    st = [d for d in wd if d["affix_type"] == side]
                    if not st:
                        continue
                    lords = {d.get("lord") for d in st}
                    lord = next(iter(lords)) if len(lords) == 1 else None
                    if not lord_ok:
                        lord = None   # armour/off-hand can't use lord-forcing omens
                    necro = "Sinistral Necromancy" if side == "Prefix" else "Dextral Necromancy"
                    lo = {"amanamu": "Omen of the Liege", "ulaman": "Omen of the Sovereign",
                          "kurgal": "Omen of the Blackblooded"}.get(lord)
                    # per-attempt cost: one bone plus the omens consumed by that
                    # desecration (the side omen, and the lord omen if we force one).
                    # NO Omen of Putrefaction: that replaces every mod and corrupts.
                    ac = (bone_cost or 8) + prices.get(f"Omen of {necro}", 5) + (prices.get(lo, 5) if lo else 0)
                    plan = PF.plan_putrefaction(base, side, target_count=len(st),
                                                lord=lord, attempt_cost=ac)
                    if plan:
                        tgt_texts = [by_id[d["mod_id"]].text[0] if d["mod_id"] in by_id
                                     else d.get("text", d["mod_id"]) for d in st]
                        recs.append({"side": side,
                                     "targets": tgt_texts,
                                     "lord": lord, "lord_omen": lo,
                                     "necro_omen": f"Omen of {necro}",
                                     "pool_size": plan.pool_size, "p_hit": round(plan.p_hit, 3),
                                     "expected_attempts": round(plan.expected_attempts, 1),
                                     "expected_cost": round(plan.expected_cost, 1),
                                     "attempt_cost": round(ac, 1), "note": plan.note,
                                     "why_omen": (f"narrows the reveal pool to {plan.pool_size} "
                                                  f"{'(one lord only) ' if lo else ''}mod"
                                                  f"{'s' if plan.pool_size!=1 else ''}, "
                                                  f"making your target ~{round(plan.p_hit*100)}% per reveal")})
                        shopping[f"Omen of {necro}"] = f"~{plan.expected_attempts:.0f} (forces {side.lower()} side)"
                        if lo:
                            shopping[lo] = f"~{plan.expected_attempts:.0f} (lord-forcing, narrows pool)"
                if not recs:
                    return None

                # craft-specific step list
                if set(wanted) <= desec_ids:
                    base_open = (f"BASE: start with a Rare <b>{base_label}</b> that has OPEN affix slots on "
                                 f"the side(s) you're desecrating. Each desecration ADDS one unrevealed mod, "
                                 f"so you desecrate + reveal once per desecrated mod you want. Do NOT use Omen "
                                 f"of Putrefaction here: it replaces every mod and CORRUPTS the item. Keep it "
                                 f"un-corrupted.")
                else:
                    base_open = (f"BASE: get your <b>{base_label}</b> to RARE with your non-desecrated mod(s) "
                                 f"secured FIRST and an OPEN slot on the target side. Desecration ADDS to the "
                                 f"item, it does not replace, and it must not be corrupted beforehand.")
                # which side(s) we're forcing, named specifically
                sides_used = [r["side"] for r in recs]
                necro_named = recs[0]["necro_omen"]
                lord_recs = [r for r in recs if r.get("lord_omen")]
                lord_named = lord_recs[0]["lord_omen"] if lord_recs else None
                targets_have_lord = any(d.get("lord") for d in wd)
                lord_note = None
                if targets_have_lord and not lord_ok:
                    lord_note = ("NOTE: lord-forcing omens (Liege / Sovereign / Blackblooded) only work on "
                                 "Weapon and Jewellery desecrations, so on this armour or off-hand piece you "
                                 "cannot narrow to a single lord. The reveal pool is the full side pool, which "
                                 "lowers your per-reveal odds.")
                desec_line = (f"DESECRATE: with the omen(s) active, use an <b>Abyssal {bone}</b> "
                              f"({bone_kind} bone). {necro_named} forces the new unrevealed mod onto the "
                              f"<b>{sides_used[0].lower()}</b> side. That's where your target "
                              f"(<i>{recs[0]['targets'][0]}</i>) lives.")
                lord_line = None
                if lord_recs:
                    lr = lord_recs[0]
                    lord_line = (f"⭐ WHY THE LORD OMEN: {lr['lord_omen']} {lr['why_omen']}. Without it the "
                                 f"pool is larger and your odds drop.")
                how = [base_open]
                if lord_note:
                    how.append(lord_note)
                if essence_step:
                    how.append(essence_step)
                how.append("⚠️ ACTIVATE YOUR OMENS FIRST, before you touch the bone: right-click "
                           f"<b>{necro_named}</b>"
                           + (f" and <b>{lord_named}</b>" if lord_named else "")
                           + " in your inventory so each shows the ACTIVE (red) border. Omens apply to your "
                             "NEXT desecration and are consumed by it, so they must already be active when you "
                             "use the bone.")
                if lord_line:
                    how.append(lord_line)
                how.append(desec_line)
                how.append("REVEAL at the Well of Souls: reveal the desecrated mod(s) one slot at a time. "
                            "Save your highest-value target for the LAST reveal of that side, since taking a mod "
                            "blocks its group on later reveals.")
                how.append("⭐ ABYSSAL ECHOES (situational, ~99 ex): if the rest of the item already rolled "
                            "high and this reveal is make-or-break, hold an Omen of Abyssal Echoes for a SECOND "
                            "chance at the reveal. Only worth it on an item already worth saving.")
                if not (set(wanted) <= desec_ids):
                    how.append("FILL REMAINING SLOTS after a good reveal: use Exalted Orbs for the rest. "
                               "Greater Exalted (min mod lvl 44) or Perfect Exalted (~50) skip weak tiers; "
                               "pair a Perfect Exalted with an Omen of Greater Exaltation to add TWO mods at once.")
                how.append("FINISH LAST: quality to 20%, add sockets + runes at the end. Plain desecration "
                           "does NOT corrupt the item, so you can finish it normally and Exalt any open slots.")

                # shopping list extras
                shopping["Exalted Orbs"] = "a few (fill non-target slots)" if not (set(wanted) <= desec_ids) else "0 (all slots desecrated)"
                total_attempt = sum(r["expected_cost"] for r in recs)
                shopping["≈ total budget"] = f"~{round(total_attempt)} ex (estimate, reveal odds unpublished)"

                return {"applies": True, "recs": recs, "how": how,
                        "base_label": base_label, "bone": f"Abyssal {bone}",
                        "shopping": [{"item": k, "qty": v} for k, v in shopping.items()],
                        "estimate_flag": "Reveal odds are unpublished by GGG, so they are modeled flat (uniform) and attempt counts and totals are ballpark. The METHOD/sequence is verified from current 0.5 crafting guides; the per-step odds are estimates."}
            except Exception:
                return None
        puf = _puf_early()
        if puf:
            result["putrefaction"] = puf
        if total == float("inf"):
            if puf:
                result["bricked"] = False
                result["msg"] = ("This mod set can't be reached by orb-slamming, so it "
                                 "needs desecrated mods. Use desecration (below).")
            else:
                result["msg"] = "No path to this mod set under modeled methods."
        else:  # not_viable
            base_msg = ("Targeting this many specific mods by orb-slamming isn't "
                        "cost-viable (expected cost is astronomical).")
            result["msg"] = (base_msg + " Use desecration (below); it adds desecrated "
                             "mods from the Well of Souls." if puf else
                             base_msg + " This is why desecration exists.")
        return jsonify(result)

    # walk policy along most-likely outcomes
    s, seen, step = start, set(), 0
    while not sv.is_goal(s) and step < 16:
        if s in seen:
            break
        seen.add(s); step += 1
        action = pol.get(s)
        if not action:
            break
        acts = sv.actions(s)
        cost = next(c for n, c, o in acts if n == action)
        outs = next(o for n, c, o in acts if n == action)
        wanted_ids = (sv.wanted_pre | sv.wanted_suf
                      | sv.desec_wanted_pre_ids | sv.desec_wanted_suf_ids)
        # A secured mod counts as progress if it belongs to a wanted GROUP (the
        # solver secures ANY acceptable tier in a wanted group, not only the exact
        # requested tier id), OR it's a specifically wanted desecrated mod.
        def _is_wanted(mid):
            if mid in wanted_ids:
                return True
            mm = sv.mods.get(mid)
            return bool(mm and mm.group in sv.wanted_groups)
        def _newly_wanted(ns):
            return {mid for mid in (ns.secured - s.secured) if _is_wanted(mid)}
        # --- Advance the walk toward the goal. The policy is a TREE; to render a
        # single readable sequence we follow the branch that best progresses
        # toward the goal. Progress = (a) securing a new wanted mod, or failing
        # that (b) the successor with the lowest expected remaining cost (which is
        # how a pure rarity upgrade like Transmute/Regal moves Normal->Magic->Rare
        # toward being able to hold the remaining mods). This prevents the walk
        # from stalling at a non-goal Magic state or following a failure branch. ---
        def _progress_key(po):
            p, ns = po
            secures = len(_newly_wanted(ns))
            er = E_.get(ns, float("inf"))
            # 1) more newly-secured wanted mods is better
            # 2) then lower expected remaining cost (closer to goal)
            # 3) then higher probability (the realistic branch)
            return (secures,
                    -er if er != float("inf") else float("-inf"),
                    p)
        success_state = max(outs, key=_progress_key)[1]
        # What THIS step secures, on the success branch we actually follow. The
        # action's outcome distribution may include several different single-mod
        # landings (an Exalted/Augment can drop any open wanted mod); listing all
        # of them would read as if one orb secured several mods, which is false.
        # We report only the mod(s) newly secured on the branch the walk follows,
        # and p_use is the total probability the step secures ANY wanted mod (the
        # honest per-attempt success chance for that step).
        useful, p_use = [], 0.0
        for p, ns in outs:
            if _newly_wanted(ns):
                p_use += p
        for mid in _newly_wanted(success_state):
            useful.append({"mod": mid, "text": _mod_text(mid),
                           "p": round(p_use, 4)})

        fail_mass = 0.0
        worst_recovery = None         # highest expected recovery cost among fail branches
        fail_next_action = None       # what the policy does from the (most likely) fail state
        bricked_mass = 0.0            # probability this step dead-ends the item
        for p, ns in outs:
            if _newly_wanted(ns):
                continue              # this outcome secured a wanted mod - not a failure
            fail_mass += p
            rec = E_.get(ns, float("inf"))
            if rec == float("inf"):
                bricked_mass += p     # no path forward from here = bricked
            else:
                if worst_recovery is None or rec > worst_recovery:
                    worst_recovery = rec
                    fail_next_action = pol.get(ns)
        on_fail = None
        if fail_mass > 1e-9 and not action.startswith("Restart"):
            if bricked_mass > 1e-9 and worst_recovery is None:
                on_fail = {
                    "p": round(fail_mass, 4),
                    "outcome": "bricked",
                    "advice": "If this fails here the item is bricked (no modeled "
                              "path forward) - start over with a fresh base."}
            else:
                _na = (fail_next_action or "").lower()
                _removal = "annul" in _na   # next move removes a mod
                _removal_note = ""
                if _removal:
                    _removal_note = (" To remove an unwanted mod you use an Orb of "
                        "Annulment, but it removes a RANDOM mod, so it's only SAFE "
                        "when every mod on the item is junk. If you have a mod you "
                        "want to keep, annulling risks deleting it (a Sinistral/"
                        "Dextral Annulment omen restricts removal to one side, which is safer).")
                on_fail = {
                    "p": round(fail_mass, 4),
                    "outcome": "recoverable",
                    "next_action": fail_next_action,
                    "is_removal": _removal,
                    "recovery_cost": (round(worst_recovery, 2)
                                      if worst_recovery is not None else None),
                    "bricked_p": (round(bricked_mass, 4) if bricked_mass > 1e-9 else 0),
                    "advice": (f"If you don't hit a wanted mod (~{round(fail_mass*100)}% "
                               f"chance), the optimal next move is "
                               f"'{fail_next_action}'." + _removal_note +
                               (f" Some failure branches brick the item "
                                f"(~{round(bricked_mass*100)}% of the time) - "
                                f"those need a fresh base."
                                if bricked_mass > 1e-9 else ""))}

        # A step that secures no wanted mod but still advances the craft is a
        # SETUP move: it changes rarity so the next step can proceed (e.g. an
        # Augment fills the Magic item so a Regal can upgrade it to Rare, opening
        # a third slot). Label it so an empty "secures" line isn't confusing.
        setup_note = None
        if not useful and not action.startswith("Restart"):
            if success_state.rarity != s.rarity:
                setup_note = (f"setup: upgrades the item to {success_state.rarity} so the "
                              f"next step can add another mod")
            else:
                setup_note = "setup: prepares the item for the next step"

        result["steps"].append({
            "n": step, "rarity": s.rarity, "action": action,
            "cost_each": round(cost, 4),
            "p_useful": round(p_use, 4),
            "expected_attempts": (round(1 / p_use, 1) if p_use > 0 else None),
            "expected_remaining": round(E_[s], 2),
            "secures": useful,
            "setup_note": setup_note,
            "is_essence": action.startswith("Essence"),
            "on_fail": on_fail,
        })
        s = success_state

    # If the player checked Essence but the plan never uses one, explain why.
    # Common cause: the item is already Rare and only a Perfect essence can act on
    # a Rare (remove+add), but none forces the wanted mod; or no essence reaches
    # the wanted tier. Surfacing this avoids the confusing "I asked for essence
    # but it slammed orbs" experience.
    if enabled_methods and "essence" in enabled_methods:
        used_essence = any(st["action"].startswith("Essence") for st in result["steps"])
        if not used_essence and result["steps"]:
            if start_rarity == "Rare":
                result["essence_note"] = ("You enabled Essence, but the item starts as a Rare. "
                    "Lesser/Greater essences only work on Normal/Magic items; on a Rare only a "
                    "Perfect Essence can act (it removes a random mod, then adds one), and none "
                    "forces your target here. So the plan adds mods with orbs instead. To use a "
                    "guaranteed essence, start from a white (Normal) base.")
            else:
                result["essence_note"] = ("You enabled Essence, but no essence forces your target "
                    "at the tier you picked, so the plan slams orbs to reach it. Lower the target "
                    "tier to the highest tier an essence covers (look for the ⚗ essence tag on a "
                    "mod) if you want a guaranteed essence path.")

    return jsonify(result)



if __name__ == "__main__":
    import os as _os
    _wired = __import__("trade_client").has_session()
    _mode = _os.environ.get("MARKET_ACCESS_MODE", "open").lower()
    print("CraftPath (for Divine Intent) · http://127.0.0.1:5000")
    print("  crafting optimizer: FREE (solver, odds, costs; works for everyone)")
    print(f"  market access mode: {_mode}")
    print(f"  trade market: {'LIVE (POESESSID detected; sell prices on)' if _wired else 'OFF (set POESESSID locally to enable live sell prices)'}")
    # use_reloader=False so this single process keeps the POESESSID from the shell
    app.run(host="127.0.0.1", port=5000, debug=True, use_reloader=False)
