"""
build_weights.py — extract REAL per-base mod weights from Path of Building's
ModItem.lua and re-weight (and base-validity-filter) every per-base mod file.

PoB encodes each mod with parallel weightKey/weightVal arrays: a list of base
TAGS and the spawn weight for each. Weight 0 (or no matching tag) = the mod
cannot roll on that base. We resolve each of our base tokens to the most
specific PoB tag it carries and pull the real weight; mods that resolve to 0 are
dropped from that base's pool (they can't actually appear there).

This is the authoritative game source, replacing the flat_uniform placeholder
weights on all non-dagger bases AND fixing base-validity (e.g. desecrated/normal
mods that were shown on bases they can't roll on).

Run:  python build_weights.py            # rewrites data/*_mods.json in place
      python build_weights.py --dry      # report only, no writes
"""
from __future__ import annotations
import re, json, os, sys, glob

POB = "/home/claude/pob2/src/Data/ModItem.lua"
DATA = os.path.join(os.path.dirname(__file__), "data")

# Each base token -> ordered list of PoB weightKey tags, MOST SPECIFIC FIRST.
# The resolver uses the first tag that appears in a given mod's weightKey; if
# none match, the mod's 'default' weight applies (usually 0 = can't roll).
BASE_TAGS = {
    "amulet":        ["amulet", "jewellery"],
    "ring":          ["ring", "jewellery"],
    "belt":          ["belt", "jewellery"],
    "talisman":      ["talisman", "amulet", "jewellery"],
    "quiver":        ["quiver"],
    "focus":         ["focus", "int_armour"],
    # one-hand martial weapons
    "claw":          ["claw", "one_hand_weapon", "weapon"],
    "dagger":        ["dagger", "one_hand_weapon", "weapon"],
    "one_hand_axe":  ["axe", "one_hand_weapon", "weapon"],
    "one_hand_mace": ["mace", "one_hand_weapon", "weapon"],
    "one_hand_sword":["sword", "one_hand_weapon", "weapon"],
    "spear":         ["spear", "one_hand_weapon", "weapon"],
    "flail":         ["flail", "one_hand_weapon", "weapon"],
    "sceptre":       ["sceptre", "one_hand_weapon", "weapon"],
    "wand":          ["wand", "one_hand_weapon", "weapon"],
    # two-hand / ranged
    "bow":           ["bow", "two_hand_weapon", "ranged", "weapon"],
    "crossbow":      ["crossbow", "two_hand_weapon", "ranged", "weapon"],
    "staff":         ["staff", "two_hand_weapon", "weapon"],
    "quarterstaff":  ["warstaff", "two_hand_weapon", "weapon"],
    "two_hand_axe":  ["axe", "two_hand_weapon", "weapon"],
    "two_hand_mace": ["mace", "two_hand_weapon", "weapon"],
    "two_hand_sword":["sword", "two_hand_weapon", "weapon"],
    # armour: <slot>_<attr_combo> -> attr-specific armour tag + slot + armour
    # attribute armour tags in PoB: str_armour, dex_armour, int_armour,
    # str_dex_armour, str_int_armour, dex_int_armour, str_dex_int_armour
}
# build armour entries programmatically
_SLOTS = {"body": "body_armour", "boots": "boots", "gloves": "gloves", "helmet": "helmet"}
_ATTR = {"str": "str_armour", "dex": "dex_armour", "int": "int_armour",
         "str_dex": "str_dex_armour", "str_int": "str_int_armour",
         "dex_int": "dex_int_armour", "str_dex_int": "str_dex_int_armour"}
for slot, slot_tag in _SLOTS.items():
    for attr, attr_tag in _ATTR.items():
        BASE_TAGS[f"{slot}_{attr}"] = [attr_tag, slot_tag, "armour"]
# shields: str_shield / dex_shield(none) ... PoB uses str_shield, str_dex_shield,
# str_int_shield (+ generic shield). dex/int-only shields fall back to 'shield'.
_SHIELD = {"str": ["str_shield", "shield"], "dex": ["shield"], "int": ["shield"],
           "str_dex": ["str_dex_shield", "str_shield", "shield"],
           "str_int": ["str_int_shield", "str_shield", "shield"],
           "dex_int": ["shield"]}
for attr, tags in _SHIELD.items():
    BASE_TAGS[f"shield_{attr}"] = tags


def parse_pob_weights(path: str) -> dict:
    """Return {mod_id: {tag: weight_int}} for every mod in ModItem.lua."""
    txt = open(path, encoding="utf-8", errors="ignore").read()
    out = {}
    # each mod is a line: ["Id"] = { ... },  — match id then its weightKey/Val
    for m in re.finditer(r'\["([^"]+)"\]\s*=\s*\{', txt):
        mid = m.group(1)
        # slice from here to the next top-level mod or a reasonable bound
        start = m.end()
        chunk = txt[start:start + 4000]   # mod entries are well under 4k chars
        wk = re.search(r'weightKey = \{([^}]*)\}', chunk)
        wv = re.search(r'weightVal = \{([^}]*)\}', chunk)
        if not wk or not wv:
            continue
        keys = re.findall(r'"([^"]+)"', wk.group(1))
        vals = [int(x) for x in re.findall(r'-?\d+', wv.group(1))]
        if len(keys) != len(vals):
            n = min(len(keys), len(vals))
            keys, vals = keys[:n], vals[:n]
        out[mid] = dict(zip(keys, vals))
    return out


def weight_for_base(tagweights: dict, base_token: str) -> int | None:
    """Resolve a mod's weight on a base. None if the mod has no weight data;
    0 means present-but-cannot-roll. First matching tag wins, else 'default'."""
    tags = BASE_TAGS.get(base_token, [base_token])
    for t in tags:
        if t in tagweights:
            return tagweights[t]
    if "default" in tagweights:
        return tagweights["default"]
    return None


def rebuild(dry=False):
    pw = parse_pob_weights(POB)
    print(f"parsed {len(pw)} mods from PoB ModItem.lua")
    files = sorted(glob.glob(os.path.join(DATA, "*_mods.json")))
    summary = []
    for fp in files:
        base = os.path.basename(fp).replace("_mods.json", "")
        if base == "desecrated":
            continue   # desecrated handled separately (not in ModItem weight pool)
        blob = json.load(open(fp))
        mods = blob["mods"] if isinstance(blob, dict) else blob
        kept, dropped, reweighted, nodata = [], 0, 0, 0
        for mm in mods:
            mid = mm.get("mod_id")
            tw = pw.get(mid)
            if tw is None:
                # no PoB weight data for this id — keep at flat 1, flag
                mm["weight"] = mm.get("weight", 1) or 1
                nodata += 1
                kept.append(mm)
                continue
            w = weight_for_base(tw, base)
            if w is None or w <= 0:
                dropped += 1            # mod can't roll on this base — remove it
                continue
            if mm.get("weight") != w:
                reweighted += 1
            mm["weight"] = w
            kept.append(mm)
        src = ("pob_real" if nodata == 0 else "pob_real_partial")
        if isinstance(blob, dict):
            blob["mods"] = kept
            blob["weights_source"] = src
            blob["weights_note"] = (
                "Real per-base spawn weights from Path of Building (ModItem.lua). "
                "Mods that can't roll on this base (weight 0) are removed."
                + (f" {nodata} mods had no PoB weight data and are kept at flat 1."
                   if nodata else ""))
        else:
            blob = {"mods": kept, "weights_source": src}
        summary.append((base, len(mods), len(kept), dropped, reweighted, nodata))
        if not dry:
            json.dump(blob, open(fp, "w"), indent=1)
    print(f"\n{'base':<18}{'before':>7}{'kept':>6}{'dropped':>8}{'reweight':>9}{'nodata':>7}")
    for row in summary:
        print(f"{row[0]:<18}{row[1]:>7}{row[2]:>6}{row[3]:>8}{row[4]:>9}{row[5]:>7}")
    print(f"\n{'DRY RUN — no files written' if dry else 'files written'}")
    return summary


if __name__ == "__main__":
    rebuild(dry="--dry" in sys.argv)
