"""
build_pools.py; regenerate the COMPLETE per-base mod pool for every base from
Path of Building's ModItem.lua, fixing the incomplete original scrape (many
bases were missing 10-190+ mods, e.g. talisman had 46/239, wand 105/276).

For each base we include every PoB mod whose resolved weight for that base is
> 0 (i.e. it can actually roll there), with the real weight. Output format
exactly matches the existing per-base files so all downstream joins keep working:
  {mod_id, affix_type, group, level, text:[...], weight, source:"base"}

Preserves dagger's separate CoE weight overlay (applied at load time from
coe_weights.json; untouched here).

Run:  python build_pools.py          # rewrites data/<base>_mods.json for all bases
      python build_pools.py --dry
"""
from __future__ import annotations
import re, json, os, sys
from build_weights import BASE_TAGS, weight_for_base

POB = "/home/claude/pob2/src/Data/ModItem.lua"
DATA = os.path.join(os.path.dirname(__file__), "data")


def parse_pob_full(path: str) -> dict:
    """Return {mod_id: full mod dict} with type/affix/group/level/text/tagweights."""
    txt = open(path, encoding="utf-8", errors="ignore").read()
    out = {}
    for m in re.finditer(r'\["([^"]+)"\]\s*=\s*\{', txt):
        mid = m.group(1)
        chunk = txt[m.end():m.end() + 4000]
        typ = re.search(r'type = "(Prefix|Suffix)"', chunk)
        if not typ:
            continue
        grp = re.search(r'group = "([^"]+)"', chunk)
        lvl = re.search(r'level = (\d+)', chunk)
        aff = re.search(r'affix = "([^"]*)"', chunk)
        wk = re.search(r'weightKey = \{([^}]*)\}', chunk)
        wv = re.search(r'weightVal = \{([^}]*)\}', chunk)
        if not (wk and wv):
            continue
        keys = re.findall(r'"([^"]+)"', wk.group(1))
        vals = [int(x) for x in re.findall(r'-?\d+', wv.group(1))]
        n = min(len(keys), len(vals))
        tagw = dict(zip(keys[:n], vals[:n]))
        # text = quoted strings before statOrder, minus the type + affix tokens
        head = chunk[:chunk.find("statOrder")] if "statOrder" in chunk else chunk
        affix_val = aff.group(1) if aff else ""
        texts = [t for t in re.findall(r'"([^"]+)"', head)
                 if t not in ("Prefix", "Suffix") and t != affix_val]
        out[mid] = {
            "mod_id": mid,
            "affix_type": typ.group(1),
            "group": grp.group(1) if grp else mid,
            "level": int(lvl.group(1)) if lvl else 1,
            "text": texts,
            "_tagw": tagw,
        }
    return out


def rebuild(dry=False):
    pw = parse_pob_full(POB)
    print(f"parsed {len(pw)} full mods from PoB ModItem.lua")
    print(f"{'base':<18}{'old':>6}{'new':>6}{'added':>7}")
    for base in sorted(BASE_TAGS):
        fp = os.path.join(DATA, f"{base}_mods.json")
        old = []
        if os.path.exists(fp):
            old_blob = json.load(open(fp))
            old = old_blob.get("mods", old_blob) if isinstance(old_blob, dict) else old_blob
        old_ids = {m["mod_id"] for m in old}
        mods = []
        for mid, d in pw.items():
            w = weight_for_base(d["_tagw"], base)
            if w is None or w <= 0:
                continue
            mods.append({
                "mod_id": mid, "affix_type": d["affix_type"], "group": d["group"],
                "level": d["level"], "text": d["text"], "weight": w, "source": "base",
            })
        added = len({m["mod_id"] for m in mods} - old_ids)
        print(f"{base:<18}{len(old):>6}{len(mods):>6}{added:>7}")
        blob = {
            "base": base, "token": base, "count": len(mods),
            "weights_source": "pob_real",
            "weights_note": ("Complete per-base mod pool + real spawn weights from "
                             "Path of Building (ModItem.lua). Only mods that can roll "
                             "on this base are included."),
            "mods": mods,
        }
        if not dry:
            json.dump(blob, open(fp, "w"), indent=1)
    print("DRY RUN" if dry else "files written")


if __name__ == "__main__":
    rebuild(dry="--dry" in sys.argv)
