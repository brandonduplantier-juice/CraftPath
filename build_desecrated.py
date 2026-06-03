"""
build_desecrated.py — rebuild data/desecrated_mods.json from Path of Building's
ModVeiled.lua, which stores the Well-of-Souls (Abyss) desecrated mods with REAL
per-base weightKey/weightVal arrays AND lord tags.

This replaces the old flat scrape (195 mods shown on EVERY base regardless of
whether they can roll there). Now each desecrated mod carries its base-tag
weights, so the API can filter to a base: a mod is shown only if it can actually
roll on that base (weight > 0 for one of the base's tags).

Run:  python build_desecrated.py        # rewrites data/desecrated_mods.json
      python build_desecrated.py --dry
"""
from __future__ import annotations
import re, json, os, sys

POB_VEILED = "/home/claude/pob2/src/Data/ModVeiled.lua"
DATA = os.path.join(os.path.dirname(__file__), "data")
LORDS = ("ulaman", "amanamu", "kurgal")


def parse_veiled(path: str) -> list[dict]:
    txt = open(path, encoding="utf-8", errors="ignore").read()
    mods = []
    for m in re.finditer(r'\["(AbyssMod[^"]+)"\]\s*=\s*\{', txt):
        mid = m.group(1)
        chunk = txt[m.end():m.end() + 2500]
        typ = re.search(r'type = "(Prefix|Suffix)"', chunk)
        aff = re.search(r'affix = "([^"]*)"', chunk)
        lvl = re.search(r'level = (\d+)', chunk)
        wk = re.search(r'weightKey = \{([^}]*)\}', chunk)
        wv = re.search(r'weightVal = \{([^}]*)\}', chunk)
        keys = re.findall(r'"([^"]+)"', wk.group(1)) if wk else []
        vals = [int(x) for x in re.findall(r'-?\d+', wv.group(1))] if wv else []
        if len(keys) != len(vals):
            n = min(len(keys), len(vals)); keys, vals = keys[:n], vals[:n]
        tags = dict(zip(keys, vals))
        # lord = the *_mod tag (ulaman_mod/amanamu_mod/kurgal_mod)
        lord = next((k[:-4] for k in keys if k.endswith("_mod") and k[:-4] in LORDS), None)
        if lord is None:
            continue   # skip non-lord abyss mods (radius-jewel etc.)
        head = chunk[:chunk.find("statOrder")] if "statOrder" in chunk else chunk
        affix_val = aff.group(1) if aff else ""
        texts = [t for t in re.findall(r'"([^"]+)"', head)
                 if t not in ("Prefix", "Suffix") and t != affix_val]
        mods.append({
            "mod_id": mid,
            "affix_name": affix_val,
            "affix_type": typ.group(1) if typ else "Suffix",
            "lord": lord,
            "level": int(lvl.group(1)) if lvl else 65,
            "ilvl": int(lvl.group(1)) if lvl else 65,
            "text": " ".join(texts).strip(),
            "tags": {k: v for k, v in tags.items() if not k.endswith("_mod")},
            "source": "desecrated",
            "weight": 1,
        })
    return mods


def rebuild(dry=False):
    mods = parse_veiled(POB_VEILED)
    from collections import Counter
    print(f"parsed {len(mods)} lord desecrated mods from ModVeiled.lua")
    print("  by lord:", dict(Counter(m["lord"] for m in mods)))
    print("  by type:", dict(Counter(m["affix_type"] for m in mods)))
    blob = {
        "source": "pob_modveiled",
        "count": len(mods),
        "note": ("Well of Souls (Abyss) desecrated mods from Path of Building "
                 "ModVeiled.lua. Each carries real per-base weightKey tags so the "
                 "API filters to mods that can actually roll on a given base. "
                 "Reveal odds among valid mods are unpublished; treat as flat."),
        "weights_source": "pob_real",
        "mods": mods,
    }
    out = os.path.join(DATA, "desecrated_mods.json")
    if not dry:
        json.dump(blob, open(out, "w"), indent=1)
    print(f"{'DRY RUN' if dry else 'wrote ' + out}")
    return mods


if __name__ == "__main__":
    rebuild(dry="--dry" in sys.argv)
