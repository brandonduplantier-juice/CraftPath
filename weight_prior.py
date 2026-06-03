"""
weight_prior.py
Sets a corrected weight prior for bases lacking real CoE data.

IMPORTANT CORRECTION: I initially used a geometric tier-decay prior, but checking
the real dagger CoE weights showed that's WRONG; PoE2 weights are FLAT across
tiers within a mod group (e.g. all 8 tiers of Dexterity share weight 8). What
makes high tiers rare is the ITEM-LEVEL cutoff (the solver already filters mods
whose required level exceeds the item's ilvl), not a lower weight. Imposing tier
decay double-counted that effect and was inaccurate.

The honest prior is therefore UNIFORM weight per mod (matching how CoE represents
it: a flat per-group weight, tier rarity handled by ilvl gating elsewhere). This
is the same as the original flat placeholder in practice, so this script now just
ensures the weights_source label is honest ("flat_uniform"; tier rarity comes
from ilvl gating, between-group weight differences are unknown without CoE data).

To get REAL between-group weights, paste a base's CoE weight table (the only
source); those land in coe_weights.json as "craft_of_exile_estimate".

Usage:  python weight_prior.py
"""
from __future__ import annotations
import json, os, glob

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")


def _coe_bases():
    p = os.path.join(DATA, "coe_weights.json")
    try:
        return set(json.load(open(p)).keys()) - {"_meta"}
    except Exception:
        return set()


def main():
    coe = _coe_bases()
    fixed = []
    for path in glob.glob(os.path.join(DATA, "*_mods.json")):
        base = os.path.basename(path).replace("_mods.json", "")
        token = base.split("_")[0]
        if token in coe:
            continue
        data = json.load(open(path))
        mods = data.get("mods", [])
        if not mods:
            continue
        # CORRECT prior: uniform weight per mod (flat within group, matching CoE).
        # Tier rarity is handled by the solver's ilvl filter, not by weight.
        for m in mods:
            m["weight"] = 1
        data["weights_source"] = "flat_uniform"
        json.dump(data, open(path, "w"))
        fixed.append(base)
    print(f"set corrected flat-uniform prior on {len(fixed)} bases")
    print(f"  (tier rarity comes from the solver's item-level gating, per real CoE behavior)")
    print(f"  (skipped {len(coe)} CoE base group(s): {sorted(coe)})")
    print("  NOTE: between-group weight differences need real CoE data (paste tables to upgrade).")


if __name__ == "__main__":
    main()

