"""
profit_scanner.py  (PREMIUM feature engine)

Finds craftable items with the best expected profit margin:
    margin = estimated_sell_price - expected_craft_cost

The cost side is fully computed by the existing solver (offline, works now).
The sell side needs the official PoE2 trade API, which requires the user's
POESESSID/OAuth and a live environment; it's behind a clean interface
(MarketPriceProvider) that returns a clearly-flagged placeholder until wired.

CANDIDATE GENERATION (which target combos to evaluate per base) supports three
strategies, selectable or combined:
  - "meta":      popular mods pulled from poe.ninja build data (demand signal)
  - "heuristic": common desirable mods per slot (built-in curated rules)
  - "user":      explicit templates the user supplies

HONESTY: a player-driven economy arbitrages away reliable profit quickly, and
early-league prices are volatile. Output is EXPECTED math with a confidence
flag, never a guarantee. Sell prices are estimates from comps, not quotes.
"""
from __future__ import annotations
import json, os
from dataclasses import dataclass, field
from solver import Solver, State

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")


# ---------------------------------------------------------------------------
# market price provider (the trade-API-dependent half); stubbed
# ---------------------------------------------------------------------------
class MarketPriceProvider:
    """Estimates the sell price of an item with a given mod set.

    Real implementation uses trade_client (official PoE2 trade2 API). It only
    works on a machine where POESESSID is set in the environment and the live
    site is reachable; the cookie is never transmitted anywhere but to
    pathofexile.com. When unavailable, returns None and the scanner flags it.
    """
    def __init__(self, league: str, enable=False):
        self.league = league
        self.enable = enable
        self._stat_ids = None
        self.available = False
        if enable:
            try:
                import trade_client  # noqa
                trade_client._session()  # raises if POESESSID missing
                self._tc = trade_client
                p = os.path.join(DATA, "trade_stat_ids.json")
                self._stat_ids = json.load(open(p)) if os.path.exists(p) else {}
                self.available = True
            except Exception:
                self.available = False

    def estimate_sell_price(self, base: str, mod_ids: list[str], item_level: int,
                            base_type: str | None = None):
        if not self.available:
            return None
        ids = [self._stat_ids[m] for m in mod_ids if m in (self._stat_ids or {})]
        if not ids:
            return None
        cat = self._tc.category_for_base(base)
        res = self._tc.estimate_sell_price(self.league, ids,
                                           category=cat, item_level_min=item_level)
        return res["estimate"] if res else None


# ---------------------------------------------------------------------------
# candidate generators (which combos to evaluate)
# ---------------------------------------------------------------------------
@dataclass
class Candidate:
    base: str
    prefixes: list[str] = field(default_factory=list)
    suffixes: list[str] = field(default_factory=list)
    label: str = ""
    source: str = "heuristic"


def _pool(base: str):
    p = os.path.join(DATA, f"{base}_mods.json")
    return json.load(open(p))["mods"] if os.path.exists(p) else []


def heuristic_candidates(base: str, max_combos=12) -> list[Candidate]:
    """Common desirable targets per slot, from built-in rules keyed on mod text."""
    mods = _pool(base)
    pre = [m for m in mods if m["affix_type"] == "Prefix"]
    suf = [m for m in mods if m["affix_type"] == "Suffix"]
    # desirability keywords by rough slot family (weapon vs armour vs jewellery)
    WANT = ["increased Physical Damage", "Adds", "increased Attack Speed",
            "maximum Life", "maximum Energy Shield", "increased Armour",
            "to Level of all", "Critical", "Resistance", "increased Spell Damage",
            "Movement Speed", "increased Evasion"]
    def top(mods_list, kw, n=1):
        hits = [m for m in mods_list
                if any(k.lower() in (m["text"][0] if m["text"] else "").lower() for k in [kw])]
        # highest tier (lowest tier number ~ highest level gate) first
        hits.sort(key=lambda m: -m["level"])
        return hits[:n]
    out = []
    # single high-value mod targets (cheap, common to craft-and-flip)
    for kw in WANT:
        for m in top(pre, kw) + top(suf, kw):
            c = Candidate(base, label=f"{kw}", source="heuristic")
            (c.prefixes if m["affix_type"] == "Prefix" else c.suffixes).append(m["mod_id"])
            out.append(c)
        if len(out) >= max_combos:
            break
    return out[:max_combos]


def user_candidates(base: str, templates: list[dict]) -> list[Candidate]:
    """User-supplied templates: [{prefixes:[ids], suffixes:[ids], label}]."""
    return [Candidate(base, t.get("prefixes", []), t.get("suffixes", []),
                      t.get("label", "custom"), "user") for t in templates]


def meta_candidates(base: str, ninja_popular_mod_ids: list[str]) -> list[Candidate]:
    """Build candidates from popular mods (from poe.ninja build data).

    `ninja_popular_mod_ids` is the demand signal (resolved elsewhere from
    poe.ninja); we pair each popular mod present in this base's pool into a
    single-target candidate. Pairing into multi-mod combos can be layered on.
    """
    pool_ids = {m["mod_id"]: m for m in _pool(base)}
    out = []
    for mid in ninja_popular_mod_ids:
        if mid in pool_ids:
            m = pool_ids[mid]
            c = Candidate(base, label=f"meta:{mid}", source="meta")
            (c.prefixes if m["affix_type"] == "Prefix" else c.suffixes).append(mid)
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# the scanner
# ---------------------------------------------------------------------------
@dataclass
class ProfitRow:
    base: str
    label: str
    source: str
    expected_cost: float
    sell_price: float | None
    margin: float | None
    confidence: str
    prefixes: list[str]
    suffixes: list[str]


def scan(base: str, *, strategies=("heuristic",), prices, essences=None,
         item_class=None, essence_prices=None, market: MarketPriceProvider = None,
         ninja_popular=None, user_templates=None, item_level=81, mods=None):
    """Rank candidate crafts for a base by expected margin."""
    if mods is None:
        raw = _pool(base)
        class M:
            def __init__(s, d):
                s.mod_id=d["mod_id"]; s.affix_type=d["affix_type"]; s.group=d["group"]
                s.level=d["level"]; s.text=d.get("text",[]); s.weight=d.get("weight",1)
                s.source=d.get("source","base")
            def weight_for(s,_): return s.weight
            def weight_for_tags(s,_): return s.weight
        mods = [M(d) for d in raw]

    cands: list[Candidate] = []
    if "heuristic" in strategies:
        cands += heuristic_candidates(base)
    if "meta" in strategies and ninja_popular:
        cands += meta_candidates(base, ninja_popular)
    if "user" in strategies and user_templates:
        cands += user_candidates(base, user_templates)

    rows = []
    start = State("Normal", frozenset(), 0, 0)
    VIABLE_CEILING = 5000.0  # ex; above this, random-slam targeting isn't realistic
    skipped_nonviable = 0
    for c in cands:
        wanted = list(c.prefixes) + list(c.suffixes)
        if not wanted or len(c.prefixes) > 3 or len(c.suffixes) > 3:
            continue
        sv = Solver(mods, base, item_level, wanted, prices,
                    essences=essences, item_class=item_class, essence_prices=essence_prices)
        E, _ = sv.solve(start)
        cost = E.get(start, float("inf"))
        if cost == float("inf") or cost > VIABLE_CEILING:
            # Targeting one specific top-tier mod via random slams costs absurd
            # amounts (often >1e6 ex). That's not how anyone crafts; it's a
            # meaningless candidate. Skip it rather than report nonsense.
            skipped_nonviable += 1
            continue
        sell = market.estimate_sell_price(base, wanted, item_level) if market else None
        margin = (sell - cost) if (sell is not None) else None
        conf = "estimate" if sell is not None else "cost_only (sell price unavailable)"
        rows.append(ProfitRow(base, c.label, c.source, round(cost, 2),
                              (round(sell, 2) if sell is not None else None),
                              (round(margin, 2) if margin is not None else None),
                              conf, c.prefixes, c.suffixes))
    # rank: by margin when known, else by cheapest cost (cheap craft-and-flip candidates)
    rows.sort(key=lambda r: (r.margin is None, -(r.margin or 0), r.expected_cost))
    if not rows and skipped_nonviable:
        # All candidates were non-viable single-mod-slam targets. Be honest about it.
        return [ProfitRow(base, "(no viable single-slam craft; use putrefaction mode)",
                          "advisory", 0.0, None, None,
                          "Single-target orb-slamming is not cost-viable for this base; "
                          "the putrefaction scanner is the realistic profit method.",
                          (), ())]
    return rows


# ---------------------------------------------------------------------------
# PUTREFACTION-BASED scan: the realistic profit craft from the 0.5 videos.
# Instead of orb-slamming one mod, model the real craft: putrefaction-slam a
# base (~36 ex/attempt) to roll a multi-stat item, priced against live comps for
# that whole stat combination. This is what people actually profit-craft.
# ---------------------------------------------------------------------------

# Realistic sellable templates per slot family: the stat KEYWORDS buyers search
# for together. Each template is what a finished, valuable item looks like.
PUTREFACTION_TEMPLATES = {
    "boots": [
        {"label": "MS + life + 2 res",
         "must": ["Movement Speed"], "nice": ["maximum Life", "Resistance"]},
        {"label": "MS + ES + res",
         "must": ["Movement Speed"], "nice": ["Energy Shield", "Resistance"]},
    ],
    "body": [
        {"label": "life + 2 res + defence",
         "must": [], "nice": ["maximum Life", "Resistance", "Energy Shield", "Evasion", "Armour"]},
        {"label": "ES + 2 res",
         "must": [], "nice": ["Energy Shield", "Resistance"]},
    ],
    "gloves": [
        {"label": "life + res + attack",
         "must": [], "nice": ["maximum Life", "Resistance", "Attack Speed"]},
    ],
    "helmet": [
        {"label": "life + 2 res",
         "must": [], "nice": ["maximum Life", "Resistance", "Energy Shield"]},
    ],
    # weapon templates grounded in VERIFIED desecrated mods + lord-forcing.
    # bow/spear: Amanamu carries the attack-speed suffix -> Liege-force it.
    "bow": [
        {"label": "phys/ele + Amanamu attack speed (Liege)",
         "must": ["Attack Speed"], "nice": [], "affix": "Suffix", "lord": "amanamu"},
        {"label": "elemental penetration (lord-forced)",
         "must": [], "nice": ["Penetrate"], "affix": "Prefix"},
    ],
    "spear": [
        {"label": "phys + Amanamu attack speed (Liege)",
         "must": ["Attack Speed"], "nice": [], "affix": "Suffix", "lord": "amanamu"},
    ],
    "crossbow": [
        {"label": "reload speed + ele pen",
         "must": [], "nice": ["Reload", "Penetrate"], "affix": "Suffix", "lord": "kurgal"},
    ],
    "quarterstaff": [
        {"label": "elemental damage (lord-forced prefix)",
         "must": [], "nice": ["increased"], "affix": "Prefix"},
    ],
    "amulet": [
        {"label": "+1 all skills (Ulaman) + res",
         "must": ["Level of all Skills"], "nice": ["Resistance"], "affix": "Suffix", "lord": "ulaman"},
    ],
    "ring": [
        {"label": "2-3 resistances",
         "must": [], "nice": ["Resistance"], "affix": "Suffix"},
    ],
}


@dataclass
class PutrefactionProfit:
    base: str
    template: str
    p_hit: float
    attempt_cost: float
    expected_cost: float
    sell_price: float | None
    margin: float | None
    n_comps: int | None
    confidence: str
    note: str


def scan_putrefaction(base: str, *, market=None, attempt_cost=36.0,
                      item_level=81, stat_ids=None, league="Runes of Aldur"):
    """Rank realistic putrefaction crafts for a base by expected margin.

    For each template: estimate P(hitting the desirable stat shape) via the
    putrefaction model, cost = attempt_cost / P, sell = live comp price for that
    stat combination. Honest about the conservative odds (global desecrated pool).
    """
    import putrefaction as PF
    bt = base.split("_")[0]
    templates = PUTREFACTION_TEMPLATES.get(bt, [])
    if not templates:
        return []
    pool = _pool(base)
    by_text = {}
    for m in pool:
        txt = (m["text"][0] if m["text"] else "").lower()
        by_text.setdefault(m["mod_id"], txt)

    rows = []
    for tpl in templates:
        # how many desecrated suffixes match the template's desirable keywords
        # (boots MS is a suffix; res/attrs are suffixes; ES/life/armour are mixed)
        wanted_kw = [k.lower() for k in (tpl["must"] + tpl["nice"])]
        # estimate hit-probability for the primary "must" stat on its affix side,
        # using the putrefaction model (conservative: global desecrated pool).
        affix = tpl.get("affix", "Suffix")   # template can specify the affix side
        lord = tpl.get("lord")                # and a lord to force (tightens odds)
        plan = PF.plan_putrefaction(base, affix, target_count=max(1, len(tpl.get("must", [])) or 1),
                                    slots=3, attempt_cost=attempt_cost, lord=lord)
        if plan is None:
            continue
        p = plan.p_hit
        exp_cost = round(attempt_cost / p, 1) if p > 0 else None

        # live sell price: search comps for the template's key stats together.
        # Use ONE representative stat hash per keyword concept (not many hashes
        # for the same stat, which would over-constrain the AND search), and only
        # trust the normalized exalted value.
        sell = None; n = None
        if market and market.available and stat_ids:
            ids, seen_kw = [], set()
            for kw in wanted_kw:
                for m in pool:
                    txt = (m["text"][0] if m["text"] else "").lower()
                    if kw in txt and m["mod_id"] in stat_ids and kw not in seen_kw:
                        ids.append(stat_ids[m["mod_id"]]); seen_kw.add(kw)
                        break
            # cap at 3 stats: a 4-stat AND match is rare early-league and skews low
            ids = list(dict.fromkeys(ids))[:3]
            if ids:
                import trade_client as TC
                cat = TC.category_for_base(base)
                res = TC.estimate_sell_price(league, ids, category=cat,
                                             item_level_min=item_level)
                if res:
                    sell = res.get("exalted_equiv")   # ONLY the normalized value
                    n = res.get("n_comps")
        margin = (round(sell - exp_cost, 1) if (sell is not None and exp_cost) else None)
        rows.append(PutrefactionProfit(
            base=base, template=tpl["label"], p_hit=round(p, 4),
            attempt_cost=attempt_cost, expected_cost=exp_cost,
            sell_price=sell, margin=margin, n_comps=n,
            confidence="estimate" if sell is not None else "cost_only (no comps)",
            note=plan.note))
    rows.sort(key=lambda r: (r.margin is None, -(r.margin or -1e9)))
    return rows


if __name__ == "__main__":
    # offline demo: heuristic candidates for dagger, cost-only (no market wired)
    pj = os.path.join(HERE, "prices_cache.json")
    prices = json.load(open(pj))["prices"] if os.path.exists(pj) else \
             {"Transmutation Orb":0.13,"Augmentation Orb":0.085,"Regal Orb":0.44,
              "Exalted Orb":1.0,"Orb of Annulment":11.67}
    rows = scan("dagger", strategies=("heuristic",), prices=prices)
    print(f"{len(rows)} candidate crafts for dagger (cost-only; market not wired):\n")
    for r in rows[:10]:
        print(f"  {r.label:<34} cost~{r.expected_cost:>8.1f} ex  [{r.confidence}]")
