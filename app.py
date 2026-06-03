"""
app.py — Flask backend for poe2craft (Exile's Forge).

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

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

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

app = Flask(__name__, template_folder="templates", static_folder="static")


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
# STRUCTURE ONLY — no payment processor is wired.
#
# MARKET_ACCESS_MODE (env var, default 'open'):
#   'open'  - everyone gets market features, no key (CURRENT DEFAULT — assumes
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
        # else — for hosting the free optimizer where no one's POESESSID belongs.
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
        "one_hand_axe":"One Hand Axe","one_hand_mace":"One Hand Mace","one_hand_sword":"One Hand Sword",
        "two_hand_axe":"Two Hand Axe","two_hand_mace":"Two Hand Mace","two_hand_sword":"Two Hand Sword",
    }
    return SIMPLE.get(token, token.replace("_", " ").title())


def _prices():
    p = os.path.join(HERE, "prices_cache.json")
    if os.path.exists(p):
        return json.load(open(p))
    return {"prices": {}, "essence_prices": {}, "league": "unknown",
            "note": "run prices.py to populate"}


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory("templates", "forge.html")


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


@app.route("/api/mods/<base>")
def api_mods(base):
    try:
        mods, wsource = _load_mod_pool(base)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    def row(m):
        return {"id": m.mod_id, "type": m.affix_type, "group": m.group,
                "level": m.level, "weight": m.weight,
                "text": (m.text[0] if m.text else m.mod_id),
                "source": getattr(m, "source", "base")}
    pre = [row(m) for m in mods if m.affix_type == "Prefix"]
    suf = [row(m) for m in mods if m.affix_type == "Suffix"]
    return jsonify({"base": base, "weights_source": wsource,
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
        row = {"id": m["mod_id"], "type": m["affix_type"], "lord": m["lord"],
               "level": m.get("ilvl", 65), "text": m["text"], "source": "desecrated"}
        (pre if m["affix_type"] == "Prefix" else suf).append(row)
    return jsonify({
        "base": base, "available": True,
        "lord_omens_valid": D.lord_omen_valid(base),
        "prefix_note": ("Body/Gloves/Boots/Helmet have no prefix desecrated mods."
                        if bt in D.NO_PREFIX_DESECRATED else None),
        "weights_note": "Desecrated reveal weights are unpublished; shown flat.",
        "prefixes": pre, "suffixes": suf})


@app.route("/api/set-session", methods=["POST"])
def api_set_session():
    """Accept the user's POESESSID from the UI (desktop edition only).
    HARD-BLOCKED when hosted: a public server must never receive cookies."""
    if os.environ.get("DEPLOY_MODE", "").lower() == "public":
        return jsonify({"ok": False, "error": "Disabled on the hosted version. "
                        "Market features run locally only — download CraftPath Desktop."}), 403
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
    })


@app.route("/api/prices")
def api_prices():
    return jsonify(_prices())


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
                 "Profit estimates are never guarantees — a player economy "
                 "arbitrages reliable margins away and early-league prices swing."),
        "rows": [vars(r) for r in rows],
    })


@app.route("/api/profit-putrefaction/<base>")
@requires_market_access
def api_profit_putrefaction(base):
    """PREMIUM: realistic putrefaction profit craft — multi-stat templates,
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
    No guessing of combos — this sidesteps the profit-scanner's accuracy gaps by
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
    result = item_parser.parse_item(raw, pool)
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
    """Visual refinement dashboard — renders the collected aggregate data."""
    return send_from_directory("templates", "data.html")


@app.route("/api/solve", methods=["POST"])
def api_solve():
    body = request.get_json(force=True)
    base = body.get("base", "dagger")
    ilvl = int(body.get("item_level", 81))
    prefixes = body.get("prefixes", [])
    suffixes = body.get("suffixes", [])
    budget = body.get("budget")
    wanted = list(prefixes) + list(suffixes)

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
    # target — the solver upgrades Normal->Magic->Rare as it crafts. So if the
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
    # Mods already on the item (kept) don't count — they're secured, not slammed.
    already = set(have_pre) | set(have_suf)
    to_acquire = [w for w in wanted if w not in already]
    if len(to_acquire) >= 4:
        return jsonify({
            "not_viable_by_slamming": True,
            "msg": "Targeting this many new specific mods by orb-slamming isn't "
                   "cost-viable (expected cost is astronomical). This is why "
                   "putrefaction exists - check the Putrefaction odds for this "
                   "base, which roll multiple desecrated mods at once."})

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
        bt = base.split("_")[0]
        if bt not in D.NO_DESECRATED:
            blob = _desecrated_all()
            for m in blob.get("mods", []):
                if D.can_roll_desecrated(base, m["affix_type"]):
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
        _exalt_omen = 10.0   # placeholder — Ritual omen, price varies
        _exalt_omen_estimated = True
    # annul omens (Sinistral/Dextral Annulment) — real price if present
    _annul_omen = None
    for k, v in prices.items():
        if "annulment" in k.lower() and "omen" in k.lower():
            _annul_omen = v if _annul_omen is None else min(_annul_omen, v)

    sv = Solver(mods, base, ilvl, wanted, prices,
                essences=essences, item_class=item_class, essence_prices=ess_prices,
                desecrated=desecrated_pool or None,
                bone_cost=bone_cost, sinistral_omen_cost=omen_cost,
                exalt_omen_cost=_exalt_omen, annul_omen_cost=_annul_omen)
    start = State(start_rarity,
                  frozenset(have_pre + have_suf),
                  junk_pre, junk_suf)
    E_, pol = sv.solve(start)
    total = E_[start]
    solve_approx = not getattr(sv, "converged", True)
    # safety: expected cost can never be negative (all actions cost > 0). A
    # negative value would indicate a numerical degeneracy — treat as unreachable.
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
                recs = []
                for side in ("Prefix", "Suffix"):
                    st = [d for d in wd if d["affix_type"] == side]
                    if not st:
                        continue
                    lords = {d.get("lord") for d in st}
                    lord = next(iter(lords)) if len(lords) == 1 else None
                    ac = (bone_cost or 8) + (prices.get("Omen of Putrefaction", 20))
                    plan = PF.plan_putrefaction(base, side, target_count=len(st),
                                                lord=lord, attempt_cost=ac)
                    if plan:
                        lo = {"amanamu": "Omen of the Liege", "ulaman": "Omen of the Sovereign",
                              "kurgal": "Omen of the Blackblooded"}.get(lord)
                        recs.append({"side": side,
                                     "targets": [by_id[d["mod_id"]].text[0] if d["mod_id"] in by_id else d.get("text", d["mod_id"]) for d in st],
                                     "lord": lord, "lord_omen": lo,
                                     "pool_size": plan.pool_size, "p_hit": round(plan.p_hit, 3),
                                     "expected_attempts": round(plan.expected_attempts, 1),
                                     "expected_cost": round(plan.expected_cost, 1),
                                     "attempt_cost": round(ac, 1), "note": plan.note})
                if not recs:
                    return None
                return {"applies": True, "recs": recs,
                        "how": [
                            "BASE: buy a high-item-level MAGIC base with ONE good stat (hold Alt in-game to see item level — higher ilvl = better possible tiers). One clean stat leaves room to craft.",
                            "ESSENCE (one only): apply a single Greater Essence for a guaranteed strong mod (e.g. Greater Essence of Abrasion = % physical for a phys weapon). Under 0.5 rules you get ONE essence — make it count.",
                            "⚠️ ACTIVATE THE OMEN: right-click the Omen of Sinistral Necromancy in your inventory to set it ACTIVE — it does nothing unless activated. (Dextral Necromancy forces a suffix instead.) People forget this step constantly.",
                            "DESECRATE: with the omen active, use a Bone (Preserved Jawbone = weapon, Rib = armour, Collarbone = jewellery). Sinistral forces the new unrevealed mod to be a PREFIX — prefixes carry the big damage rolls, so this is usually what you want.",
                            "REVEAL at the Well of Souls: travel there and reveal the desecrated mod to see what you got.",
                            "⭐ ABYSSAL ECHOES (situational, ~99 ex): if the rest of the weapon already rolled high/perfect and this reveal is make-or-break, hold an Omen of Abyssal Echoes for a SECOND chance at the reveal. It's expensive — only worth it on an item already worth saving. Otherwise just reveal and accept the result.",
                            "FILL REMAINING SLOTS after a good reveal: use Exalted Orbs to add random mods. To skip weak low tiers, use a Greater Exalted (min mod lvl ~35) or Perfect Exalted (~50). Pair a single Perfect Exalted with an Omen of Greater Exaltation to add TWO mods at once — doubling the value of one rare orb.",
                            "FINISH LAST: quality to 20%, add sockets + runes only at the end.",
                        ],
                        "estimate_flag": "Reveal odds are unpublished by GGG — modeled flat (uniform), so attempt counts are ballpark. The METHOD/sequence is verified from current 0.5 crafting guides; the per-step odds are estimates."}
            except Exception:
                return None
        puf = _puf_early()
        if puf:
            result["putrefaction"] = puf
        if total == float("inf"):
            if puf:
                result["bricked"] = False
                result["msg"] = ("This mod set can't be reached by orb-slamming — it "
                                 "needs desecrated mods. Use Putrefaction (below).")
            else:
                result["msg"] = "No path to this mod set under modeled methods."
        else:  # not_viable
            base_msg = ("Targeting this many specific mods by orb-slamming isn't "
                        "cost-viable (expected cost is astronomical).")
            result["msg"] = (base_msg + " Use Putrefaction (below) — it rolls multiple "
                             "desecrated mods at once." if puf else
                             base_msg + " This is why putrefaction exists.")
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
        useful, p_use = [], 0.0
        _seen_mods = set()
        for p, ns in outs:
            for mid in (ns.secured - s.secured):
                p_use += p
                if mid in _seen_mods:
                    continue
                _seen_mods.add(mid)
                useful.append({"mod": mid,
                               "text": _mod_text(mid),
                               "p": round(p, 4)})

        # --- Honest failure analysis: what happens if this step does NOT secure
        # a wanted mod. We group the non-progress outcomes and report what the
        # solver's own policy says to do from there, with the real recovery cost
        # (E_[failure_state]) - no invented advice. ---
        success_state = max(outs, key=lambda po: po[0])[1]
        fail_mass = 0.0
        worst_recovery = None         # highest expected recovery cost among fail branches
        fail_next_action = None       # what the policy does from the (most likely) fail state
        bricked_mass = 0.0            # probability this step dead-ends the item
        for p, ns in outs:
            secured_new = ns.secured - s.secured
            if secured_new:
                continue              # this outcome made progress - not a failure
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
                on_fail = {
                    "p": round(fail_mass, 4),
                    "outcome": "recoverable",
                    "next_action": fail_next_action,
                    "recovery_cost": (round(worst_recovery, 2)
                                      if worst_recovery is not None else None),
                    "bricked_p": (round(bricked_mass, 4) if bricked_mass > 1e-9 else 0),
                    "advice": (f"If you don't hit a wanted mod (~{round(fail_mass*100)}% "
                               f"chance), the optimal next move is "
                               f"'{fail_next_action}'." +
                               (f" Some failure branches brick the item "
                                f"(~{round(bricked_mass*100)}% of the time) - "
                                f"those need a fresh base."
                                if bricked_mass > 1e-9 else ""))}

        result["steps"].append({
            "n": step, "rarity": s.rarity, "action": action,
            "cost_each": round(cost, 4),
            "p_useful": round(p_use, 4),
            "expected_attempts": (round(1 / p_use, 1) if p_use > 0 else None),
            "expected_remaining": round(E_[s], 2),
            "secures": useful,
            "is_essence": action.startswith("Essence"),
            "on_fail": on_fail,
        })
        s = success_state

    return jsonify(result)



if __name__ == "__main__":
    import os as _os
    _wired = __import__("trade_client").has_session()
    _mode = _os.environ.get("MARKET_ACCESS_MODE", "open").lower()
    print("CraftPath (for Divine Intent) — http://127.0.0.1:5000")
    print("  crafting optimizer: FREE (solver, odds, costs — works for everyone)")
    print(f"  market access mode: {_mode}")
    print(f"  trade market: {'LIVE (POESESSID detected — sell prices on)' if _wired else 'OFF (set POESESSID locally to enable live sell prices)'}")
    # use_reloader=False so this single process keeps the POESESSID from the shell
    app.run(host="127.0.0.1", port=5000, debug=True, use_reloader=False)
