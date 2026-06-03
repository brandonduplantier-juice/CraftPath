"""
gen_all_bases.py
Generate mod-pool JSON for every craftable base from PoB data.

Armour pieces (Body/Boots/Gloves/Helmet/Shield) come in attribute variants
(str / dex / int / hybrids) with different mod pools, so we expand those into
separate base tokens (e.g. body_str, body_dex_int). Weapons, jewellery, etc.
are single bases. Every mod is tagged by source (base / desecrated).

Output: data/<token>_mods.json for each, plus data/bases_index.json listing
all generated bases with their display label and resolved tags.
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pob_loader import parse_modfile, base_tag_sets, pool_for_base, Mod
from dataclasses import asdict

MODFILE = "/home/claude/pob2/src/Data/ModItem.lua"
BASESDIR = "/home/claude/pob2/src/Data/Bases"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Armour attribute variants -> the tag that distinguishes their pool.
# PoB tags: str_armour, dex_armour, int_armour, str_dex_armour, str_int_armour, dex_int_armour
ARMOUR_BASES = {
    "Body Armour": "body", "Boots": "boots", "Gloves": "gloves",
    "Helmet": "helmet", "Shield": "shield",
}
ATTR_VARIANTS = {
    "str": "str_armour", "dex": "dex_armour", "int": "int_armour",
    "str_dex": "str_dex_armour", "str_int": "str_int_armour", "dex_int": "dex_int_armour",
}

# Single (non-attribute-split) bases: token -> (display label, base type in PoB)
SIMPLE_BASES = {
    "amulet":"Amulet","belt":"Belt","ring":"Ring","quiver":"Quiver","focus":"Focus",
    "claw":"Claw","dagger":"Dagger","flail":"Flail","spear":"Spear","bow":"Bow",
    "crossbow":"Crossbow","staff":"Staff","talisman":"Talisman",
    "one_hand_axe":"One Hand Axe","one_hand_mace":"One Hand Mace","one_hand_sword":"One Hand Sword",
    "two_hand_axe":"Two Hand Axe","two_hand_mace":"Two Hand Mace","two_hand_sword":"Two Hand Sword",
    "sceptre":"Sceptre","wand":"Wand",
}


def dump(mods, tags, token, label):
    rows = []
    for m in mods:
        w = m.weight_for_tags(list(tags))
        if w <= 0:
            continue
        d = {"mod_id": m.mod_id, "affix_type": m.affix_type, "group": m.group,
             "level": m.level, "text": m.text, "weight": w, "source": m.source}
        rows.append(d)
    path = os.path.join(OUT, f"{token}_mods.json")
    json.dump({"base": label, "token": token, "tags": sorted(tags),
               "count": len(rows), "mods": rows}, open(path, "w"))
    pre = sum(r["affix_type"] == "Prefix" for r in rows)
    suf = len(rows) - pre
    des = sum(r["source"] == "desecrated" for r in rows)
    return len(rows), pre, suf, des


def main():
    mods = parse_modfile(MODFILE)
    tagmap = base_tag_sets(BASESDIR)
    index = []

    # simple bases
    for token, label in SIMPLE_BASES.items():
        tags = tagmap.get(label)
        if not tags:
            tags = tagmap.get(label.title()) or set()
        if not tags:
            print(f"  ! no tags for {label}, skipping"); continue
        n, pre, suf, des = dump(mods, tags, token, label)
        index.append({"token": token, "label": label, "count": n, "desecrated": des})
        print(f"  {label:<18} {n:>3} mods ({pre}p/{suf}s)")

    # armour attribute variants. Each variant rolls mods tagged with:
    #   - the slot tag (body_armour/gloves/...) and generic 'armour'
    #   - its own attribute tag(s): a pure str piece gets str_armour; a hybrid
    #     str/int piece gets str_armour + int_armour + str_int_armour
    #   - the universal str_dex_int_armour pool (rolls on every armour)
    SLOT_TAG = {"Body Armour":"body_armour","Boots":"boots","Gloves":"gloves",
                "Helmet":"helmet","Shield":"shield"}
    VARIANT_ATTR_TAGS = {
        "str":        {"str_armour"},
        "dex":        {"dex_armour"},
        "int":        {"int_armour"},
        "str_dex":    {"str_armour","dex_armour","str_dex_armour"},
        "str_int":    {"str_armour","int_armour","str_int_armour"},
        "dex_int":    {"dex_armour","int_armour","dex_int_armour"},
    }
    for label, base_token in ARMOUR_BASES.items():
        slot = SLOT_TAG[label]
        for attr, attr_tags in VARIANT_ATTR_TAGS.items():
            tags = {slot, "armour", "default", "str_dex_int_armour"} | attr_tags
            token = f"{base_token}_{attr}"
            n, pre, suf, des = dump(mods, tags, token, f"{label} ({attr.upper().replace('_','/')})")
            if n > 0:
                index.append({"token": token, "label": f"{label} ({attr.upper().replace('_','/')})",
                              "count": n, "desecrated": des})
                print(f"  {token:<18} {n:>3} mods ({pre}p/{suf}s)")

    json.dump(index, open(os.path.join(OUT, "bases_index.json"), "w"), indent=2)
    print(f"\nGenerated {len(index)} bases -> data/bases_index.json")
    print("NOTE: desecrated (Well of Souls) mods are NOT included; PoB's data has "
          "only a partial set; the full desecrated pool lives on PoE2DB's Desecrated "
          "Modifiers page and needs separate sourcing.")


if __name__ == "__main__":
    main()
