"""
planner.py
The interactive crafting loop from the original spec:

  given a target item, recommend the next step (with odds, expected attempts,
  and estimated cost); you report what actually rolled; it advances, tells you
  how to recover an unwanted mod, or declares the item bricked.

This is a GREEDY forward recommender (v1). The full cheapest / lowest-brick
optimizing search over the whole method tree is the next increment; this proves
the loop and the per-step math on real data.

Costs are PLACEHOLDERS (in Exalted-orb units) until the poe.ninja feed is wired
in. They are clearly fake and must not be read as market prices.
"""
from __future__ import annotations
from dataclasses import dataclass
from item_state import ItemState, RolledAffix
from probability import addable_pool, hit_probability, expected_attempts, prob_within
import methods as MX
from prices import load_prices

# Fallback unit costs in Exalted-equivalents, used ONLY if no live cache exists.
PLACEHOLDER_COST = {
    "Transmutation Orb": 0.01, "Augmentation Orb": 0.02, "Regal Orb": 0.2,
    "Exalted Orb": 1.0, "Orb of Annulment": 2.0, "Divine Orb": 4.0,
}

_PRICES = load_prices()   # cached poe.ninja data, or None

def cost_of(method_name: str):
    """(value_in_exalted, source). Live cache preferred; placeholder otherwise."""
    if _PRICES and method_name in _PRICES.get("prices", {}):
        return _PRICES["prices"][method_name], "live"
    return PLACEHOLDER_COST.get(method_name), "placeholder"


@dataclass
class Target:
    """Desired final mods, by exact mod_id, each tied to its group + slot."""
    wanted_ids: set[str]
    by_id: dict          # mod_id -> Mod object (for group/type lookup)

    def groups(self) -> dict:
        return {self.by_id[i].group: i for i in self.wanted_ids}


def _missing(state: ItemState, target: Target) -> set[str]:
    have = {a.mod_id for a in state.affixes}
    return target.wanted_ids - have


def _wrong_mod_in_wanted_group(state: ItemState, target: Target):
    """A filled slot whose group we want, but holding the wrong tier/mod."""
    wanted_by_group = target.groups()
    for a in state.affixes:
        if a.group in wanted_by_group and a.mod_id != wanted_by_group[a.group]:
            return a
    return None


def recommend(state: ItemState, target: Target, mods) -> dict:
    """Return the next recommended step and its scoring, or a terminal verdict."""
    missing = _missing(state, target)
    if not missing:
        return {"done": True, "msg": "Target met. Divine to perfect rolls if desired."}

    # is a slot we need occupied by the wrong mod? that requires risky removal
    blocker = _wrong_mod_in_wanted_group(state, target)
    if blocker:
        good_on_item = sum(a.mod_id in target.wanted_ids for a in state.affixes)
        total_on_item = len(state.affixes)
        p_hit_good = good_on_item / total_on_item if total_on_item else 0
        cost, src = cost_of(MX.ANNUL.name)
        return {
            "method": MX.ANNUL,
            "kind": "remove",
            "msg": (f"Slot for group '{blocker.group}' holds the wrong mod "
                    f"({blocker.mod_id}). Annul to clear it."),
            "risk_removes_wanted": round(p_hit_good, 3),
            "cost_each": cost, "price_source": src,
            "confidence": MX.ANNUL.confidence,
        }

    # otherwise we need to ADD a missing mod -> pick the right adder for rarity
    if state.rarity == "Normal":
        method = MX.TRANSMUTE
    elif state.rarity == "Magic" and state.open_slots() > 0 and len(state.affixes) < 2:
        method = MX.AUGMENT
    elif state.rarity == "Magic":
        method = MX.REGAL
    elif state.rarity == "Rare" and state.open_slots() > 0:
        method = MX.EXALT
    else:
        return {"bricked": True,
                "msg": "No open slot of the needed type and no safe recovery. Bricked."}

    # score against the state AS IT WILL BE after this method: a rarity upgrade
    # (Transmute->Magic, Regal->Rare) opens new slots, so the add must be scored
    # at the resulting rarity, not the current one.
    scoring_rarity = method.sets_rarity or state.rarity
    sstate = ItemState(state.base_token, state.item_level, scoring_rarity, list(state.affixes))
    pool = addable_pool(sstate, mods)
    pool_w = sum(m.weight_for(sstate.base_token) for m in pool)
    addable_missing = [m for m in pool if m.mod_id in missing]
    p_any = (sum(m.weight_for(sstate.base_token) for m in addable_missing) / pool_w
             if pool_w else 0.0)
    per_target = {m.mod_id: round(hit_probability(sstate, mods, m.mod_id), 4)
                  for m in addable_missing}
    ea = expected_attempts(p_any)
    cost, src = cost_of(method.name)
    return {
        "method": method,
        "kind": "add",
        "msg": f"Use {method.name} ({method.note or method.action}).",
        "p_useful": round(p_any, 4),
        "per_target_p": per_target,
        "exp_attempts_for_any": round(ea, 1) if ea != float("inf") else None,
        "p_within_10": round(prob_within(p_any, 10), 3),
        "eligible_pool_size": len(pool),
        "cost_each": cost, "price_source": src,
        "confidence": method.confidence,
    }


# --------------------------------------------------------------------------
def demo():
    import json
    raw = json.load(open("data/claw_mods.json"))["mods"]
    class M:
        def __init__(s, d): s.__dict__.update(d)
        def weight_for(s, _): return s.weight
    mods = [M(d) for d in raw]
    by_id = {m.mod_id: m for m in mods}

    # Want: top fire-damage prefix + top dexterity suffix on a high-ilvl claw.
    want = {"LocalAddedFireDamage9", "Dexterity8"}
    target = Target(want, by_id)
    pmeta = _PRICES
    if pmeta:
        print(f"PRICES: live poe.ninja, {pmeta['league']} ({pmeta['fetched_utc']})")
    else:
        print("PRICES: placeholder (run `python prices.py` to load live values)")
    print("TARGET:", {i: by_id[i].text[0] for i in want}, "\n")

    state = ItemState("claw", item_level=81, rarity="Normal")
    for step in range(1, 7):
        rec = recommend(state, target, mods)
        print(f"--- step {step} | item: {state.rarity}, "
              f"mods={[a.mod_id for a in state.affixes]} ---")
        if rec.get("done"):   print("  DONE:", rec["msg"]); break
        if rec.get("bricked"):print("  BRICKED:", rec["msg"]); break
        print("  recommend:", rec["msg"], f"[{rec['confidence']}]")
        if rec["kind"] == "add":
            ea = rec["exp_attempts_for_any"]
            unit = rec["cost_each"]
            step_cost = (round(ea * unit, 3) if ea and unit is not None else None)
            print(f"  odds of landing a wanted mod: {rec['p_useful']*100:.1f}%  "
                  f"(~{ea} tries, {rec['p_within_10']*100:.0f}% within 10)")
            print(f"  cost/try ~{unit} ex ({rec['price_source']})  ->  "
                  f"expected step cost ~{step_cost} ex")
            print(f"  per-target: {rec['per_target_p']}")
        # simulate a lucky outcome to advance the walkthrough:
        if rec["kind"] == "add":
            sets = rec["method"].sets_rarity or state.rarity
            sstate = ItemState(state.base_token, state.item_level, sets, list(state.affixes))
            tgt = next(iter(rec["per_target_p"]), None)
            if tgt is None:
                avail = addable_pool(sstate, mods)
                if not avail:
                    print("  (no eligible mod to simulate; stopping)"); break
                tgt = avail[0].mod_id
            m = by_id[tgt]
            state = ItemState(state.base_token, state.item_level, sets,
                              state.affixes + [RolledAffix(m.mod_id, m.affix_type, m.group)])


if __name__ == "__main__":
    demo()
