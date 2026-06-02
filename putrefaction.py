"""
putrefaction.py
Models the dominant 0.5 craft: Omen of Putrefaction + Bone.

MECHANIC (confirmed by multiple 0.5 crafting demonstrations):
  - Target must be a RARE, NOT already desecrated, NOT corrupted.
  - Prep first: 20% quality + sockets (Putrefaction CORRUPTS the item, and you
    can't quality/socket a corrupted item afterward — though runes still socket
    into corrupted gear).
  - Omen of Putrefaction + Bone (Rib=armour, Jawbone=weapon/quiver,
    Collarbone=jewellery) replaces ALL mods with up to 6 UNREVEALED desecrated
    mods (3 prefix slots + 3 suffix slots) and corrupts the item.
  - Reveal at the Well of Souls one slot at a time. PREFIXES reveal first (all
    three), then suffixes. Each reveal offers a few options to choose from.
  - GROUP BLOCKING: taking a mod blocks its group on later reveals, and taking a
    low roll of a desirable mod (e.g. low movement speed) blocks better rolls of
    it — so you save high-value targets for the last slot of that type.
  - Lord-forcing omens (Sovereign=Ulaman, Liege=Amanamu, Blackblooded=Kurgal)
    restrict the reveal pool to ONE lord's mods. This is the key to targeting:
    a small lord pool (e.g. Amanamu has only ~3 weapon mods) makes a specific
    mod very likely.

COST (from demonstrations, ~ values, league-start):
  putrefaction omen ~8 ex, bone ~8 ex, quality (scraps) ~11 ex, runes ~4 ex ea.
  ~36 ex per attempt for boots/body. Bases 1-5 ex.

This module computes the probability of hitting target desecrated mod(s) given
the slot count, the desecrated pool size, options-per-reveal, and optional
lord-forcing. Reveal weights are unpublished, so the pool is treated uniformly
and the result is FLAGGED as an estimate.
"""
from __future__ import annotations
import json, os
from dataclasses import dataclass

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

# default options shown per reveal (the "casino" shows a few; demos suggest ~3)
DEFAULT_OPTIONS_PER_REVEAL = 3


def _desec_pool():
    p = os.path.join(DATA, "desecrated_mods.json")
    return json.load(open(p))["mods"] if os.path.exists(p) else []


def _per_base_pool_size(base_token: str, affix_type: str):
    """Return the REAL desecrated pool size for this base+affix from the per-base
    table (PoE2DB per-base pages), or None if the base isn't catalogued yet.
    For armour, putrefaction PREFIX slots reveal from the NORMAL prefix pool
    (marked desec_prefix_from_normal), so we return that count instead."""
    p = os.path.join(DATA, "desecrated_per_base.json")
    try:
        tbl = json.load(open(p))["bases"]
    except Exception:
        return None, None
    bt = base_token.split("_")[0]
    entry = tbl.get(bt)
    if not entry:
        return None, None
    if affix_type == "Prefix":
        if entry.get("desec_prefix_from_normal"):
            # prefix reveal draws from the normal prefix pool for this base
            n = _normal_pool_size(base_token, "Prefix")
            return (n, "normal-prefix-pool (armour)") if n else (entry.get("prefix"), entry.get("source",""))
        return entry.get("prefix"), entry.get("source", "")
    return entry.get("suffix"), entry.get("source", "")


def _normal_pool_size(base_token: str, affix_type: str):
    """Count normal mods of an affix type in this base's pool (for armour
    putrefaction prefix reveals, which draw from the normal pool)."""
    p = os.path.join(DATA, f"{base_token}_mods.json")
    if not os.path.exists(p):
        # try the bare token (e.g. 'boots' if 'boots_dex' missing)
        p = os.path.join(DATA, f"{base_token.split('_')[0]}_mods.json")
        if not os.path.exists(p):
            return None
    mods = json.load(open(p)).get("mods", [])
    # count distinct mod GROUPS of this affix (a reveal offers distinct groups)
    groups = {m.get("group") for m in mods if m.get("affix_type") == affix_type}
    return len(groups) if groups else None


def _pool_for(affix_type: str, lord: str | None):
    pool = [m for m in _desec_pool() if m["affix_type"] == affix_type]
    if lord:
        pool = [m for m in pool if m["lord"] == lord]
    return pool


def p_hit_one_target(pool_size: int, slots: int, options_per_reveal: int,
                     n_targets: int = 1) -> float:
    """P(reveal at least one of n_targets desirable mods) over `slots` reveals,
    each showing `options_per_reveal` distinct options from a pool of pool_size,
    choosing the best. Uniform pool (reveal weights unpublished).

    Per reveal, P(a target appears among the shown options) =
       1 - C(pool_size - n_targets, k) / C(pool_size, k)
    Over `slots` independent reveals (group-blocking only helps, so this is a
    conservative lower bound), P(>=1 hit) = 1 - (1 - p_one)^slots.
    """
    from math import comb
    if pool_size <= 0 or n_targets <= 0:
        return 0.0
    k = min(options_per_reveal, pool_size)
    nt = min(n_targets, pool_size)
    p_one = 1.0 - (comb(pool_size - nt, k) / comb(pool_size, k) if pool_size - nt >= k else 0.0)
    return 1.0 - (1.0 - p_one) ** max(1, slots)


@dataclass
class PutrefactionPlan:
    base: str
    affix_type: str            # which side the target sits on
    target_count: int          # how many desirable mods qualify as a "hit"
    lord: str | None
    pool_size: int
    slots: int
    options_per_reveal: int
    p_hit: float
    attempt_cost: float
    expected_attempts: float
    expected_cost: float
    note: str


def plan_putrefaction(base: str, affix_type: str, *, target_count=1, lord=None,
                      slots=3, options_per_reveal=DEFAULT_OPTIONS_PER_REVEAL,
                      attempt_cost=36.0, fractured=False):
    """Estimate putrefaction odds & cost for hitting a target on one affix side.
    `target_count` = how many mods in the (lord-filtered) pool you'd be happy with.

    Putrefaction always rolls the MAX desecrated mods for the base — typically 6
    (3 prefix + 3 suffix), or 5 if the item is fractured (confirmed PoE2 wiki).
    `fractured=True` reduces the affix side being revealed by one slot, modeling
    a pre-fractured mod occupying a slot.
    """
    import desecrated as D
    import json as _json
    bt = base.split("_")[0]
    if fractured and slots > 1:
        slots = slots - 1   # a fractured mod consumes one of the 6 -> 5 total
    if bt in D.NO_DESECRATED:
        return None
    # slot rule: armour has no EXCLUSIVE desecrated prefixes — but putrefaction
    # still creates prefix slots that reveal NORMAL prefixes (e.g. boots -> MS).
    # Only block prefix putrefaction if the base neither has desec prefixes nor
    # draws prefixes from the normal pool.
    if affix_type == "Prefix" and bt in D.NO_PREFIX_DESECRATED:
        try:
            entry = _json.load(open(os.path.join(DATA, "desecrated_per_base.json")))["bases"].get(bt, {})
        except Exception:
            entry = {}
        if not entry.get("desec_prefix_from_normal"):
            return None
    pool = _pool_for(affix_type, lord)
    global_size = len(pool)
    # prefer the REAL per-base pool size; fall back to global (flagged) if base
    # not yet catalogued.
    per_base, src = _per_base_pool_size(base, affix_type)
    # lord-forcing: if a lord omen is specified, use that lord's per-base subset
    if lord and per_base is not None:
        try:
            tbl = _json.load(open(os.path.join(DATA, "desecrated_per_base.json")))["bases"]
            split = tbl.get(bt, {}).get("lord_split", {})
            if lord in split:
                idx = 0 if affix_type == "Prefix" else 1
                per_base = split[lord][idx]
                src = f"{src} | lord-forced:{lord}"
        except Exception:
            pass
    if per_base is not None:
        pool_size = per_base
        pool_note = f"per-base pool ({src})"
    else:
        pool_size = global_size
        pool_note = "GLOBAL pool (base not yet catalogued — conservative)"
    if pool_size == 0:
        return None
    p = p_hit_one_target(pool_size, slots, options_per_reveal, target_count)
    exp_attempts = (1.0 / p) if p > 0 else float("inf")
    return PutrefactionPlan(
        base=base, affix_type=affix_type, target_count=target_count, lord=lord,
        pool_size=pool_size, slots=slots, options_per_reveal=options_per_reveal,
        p_hit=round(p, 4), attempt_cost=attempt_cost,
        expected_attempts=round(exp_attempts, 2) if p > 0 else None,
        expected_cost=round(attempt_cost / p, 1) if p > 0 else None,
        note=("Lord-forced to %s. " % lord if lord else "Open reveal. ") +
             ("Using %s. " % pool_note) +
             ("Reveal weights unpublished (treated uniform). " if per_base is not None
              else "CONSERVATIVE: global pool, not yet per-base filtered, so real "
                   "odds are BETTER than shown. Reveal weights also unpublished."))


if __name__ == "__main__":
    # the bow craft from the transcript: force Amanamu suffix, want attack speed.
    print("=== Bow: force Amanamu suffix, target attack speed (transcript craft) ===")
    pool = _pool_for("Suffix", "amanamu")
    print(f"Amanamu suffix pool size: {len(pool)}")
    for m in pool:
        if "Attack Speed" in m["text"] or "Pierce" in m["text"] or "Spirit Reservation" in m["text"]:
            print(f"   candidate: {m['text'][:55]}")
    plan = plan_putrefaction("bow", "Suffix", target_count=1, lord="amanamu", slots=3)
    if plan:
        print(f"P(hit) = {plan.p_hit*100:.1f}%  expected ~{plan.expected_attempts} attempts  "
              f"~{plan.expected_cost} ex")
    print("\n=== Boots: open reveal, target 35% movement speed (1 desirable suffix) ===")
    plan2 = plan_putrefaction("boots_dex", "Suffix", target_count=1, slots=3)
    if plan2:
        print(f"pool={plan2.pool_size} P(hit)={plan2.p_hit*100:.1f}% ~{plan2.expected_cost} ex")
    print("\n=== Boots prefix (should be blocked: armour has no prefix desec) ===")
    print("blocked:", plan_putrefaction("boots_dex", "Prefix") is None)
