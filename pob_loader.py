"""
pob_loader.py
Parse Path of Building (PoE2) ModItem.lua into structured mod records.

Source of truth: PathOfBuildingCommunity/PathOfBuilding-PoE2,
  src/Data/ModItem.lua  (one mod per line, version 0.17.0 = patch 0.5 data).

Each mod line looks like:
  ["Strength1"] = { type = "Suffix", affix = "of the Brute", "+(5-8) to Strength",
     statOrder = {991}, level = 1, group = "Strength",
     weightKey = {"ring","amulet",...}, weightVal = {1,1,...}, ... },

The same fields exist (under different names) in the RePoE poe2 JSON export
(spawn_weights / groups / required_level / stats), so the rest of the engine
consumes the normalized Mod dataclass below and is source-agnostic.
"""
from __future__ import annotations
import re, json, sys
from dataclasses import dataclass, field, asdict
from typing import Optional

_KEY      = re.compile(r'^\s*\["(.+?)"\]\s*=\s*\{')
_TYPE     = re.compile(r'\btype\s*=\s*"(\w+)"')
_AFFIX    = re.compile(r'\baffix\s*=\s*"(.*?)"')
_LEVEL    = re.compile(r'\blevel\s*=\s*(\d+)')
_GROUP    = re.compile(r'\bgroup\s*=\s*"(.+?)"')
_WKEY     = re.compile(r'weightKey\s*=\s*\{(.*?)\}')
_WVAL     = re.compile(r'weightVal\s*=\s*\{(.*?)\}')
# stat lines sit between the affix field and statOrder, as bare quoted strings
_STATBLK  = re.compile(r'affix\s*=\s*"[^"]*"\s*,\s*(.*?)\s*,?\s*statOrder')
_QUOTED   = re.compile(r'"([^"]*)"')
_RANGE    = re.compile(r'\((\d+)-(\d+)\)')


@dataclass
class Mod:
    mod_id: str
    affix_type: str                 # "Prefix" | "Suffix"
    affix_name: str                 # e.g. "of the Brute"
    text: list[str]                 # raw stat lines, e.g. ["+(5-8) to Strength"]
    ranges: list[tuple[int, int]]   # numeric (min,max) pairs pulled from text
    level: int                      # item level required for this tier
    group: str                      # mods sharing a group are mutually exclusive
    weights: dict[str, int] = field(default_factory=dict)  # base_token -> weight
    _ordered_weights: list = field(default_factory=list)   # [(tag, weight)] in PoB order
    source: str = "base"                                   # base | desecrated | essence

    def weight_for(self, base_token: str) -> int:
        """Spawn weight on a given base category (0 if it cannot roll there)."""
        return self.weights.get(base_token, 0)

    def weight_for_tags(self, tags: list[str]) -> int:
        """Resolved weight for a base described by an ordered tag list.

        PoB stores weights per TAG (e.g. 'weapon', 'one_hand_weapon', 'dagger'),
        not per base. A mod's weightKey lists tags in priority order; the FIRST
        tag that the base possesses determines the weight (this is how the game
        resolves it). 'default' is the catch-all fallback. We honor that order:
        walk this mod's weight keys in their stored order and return the weight
        of the first key the base's tag set contains.
        """
        tagset = set(tags)
        for key, w in self._ordered_weights:
            if key in tagset:
                return w
        return self.weights.get("default", 0)


def _detect_source(mod_id: str) -> str:
    """Classify a mod by its crafting source from its id.
    Desecrated (Well of Souls / Abyssal) mods carry 'Abyss' + a lord name."""
    mid = mod_id.lower()
    if "abyss" in mid or "ulaman" in mid or "amanamu" in mid or "kurgal" in mid:
        return "desecrated"
    return "base"


def parse_modfile(path: str) -> list[Mod]:
    mods: list[Mod] = []
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            km = _KEY.search(line)
            tm = _TYPE.search(line)
            if not km or not tm:
                continue
            wk = _WKEY.search(line)
            wv = _WVAL.search(line)
            weights: dict[str, int] = {}
            ordered: list = []
            if wk and wv:
                keys = _QUOTED.findall(wk.group(1))
                vals = [int(x) for x in re.findall(r"-?\d+", wv.group(1))]
                weights = {k: v for k, v in zip(keys, vals)}
                ordered = list(zip(keys, vals))

            text: list[str] = []
            sb = _STATBLK.search(line)
            if sb:
                text = [t for t in _QUOTED.findall(sb.group(1)) if t]
            ranges = [(int(a), int(b)) for blk in text for a, b in _RANGE.findall(blk)]

            am = _AFFIX.search(line)
            lm = _LEVEL.search(line)
            gm = _GROUP.search(line)
            mods.append(Mod(
                mod_id=km.group(1),
                affix_type=tm.group(1),
                affix_name=am.group(1) if am else "",
                text=text,
                ranges=ranges,
                level=int(lm.group(1)) if lm else 0,
                group=gm.group(1) if gm else km.group(1),
                weights=weights,
                _ordered_weights=ordered,
                source=_detect_source(km.group(1)),
            ))
    return mods


# tag sets per base type, read from PoB Bases/*.lua (type -> sorted tag list).
_TAGS_RE = re.compile(r"tags\s*=\s*\{([^}]*)\}")
_TYPE_RE = re.compile(r'type\s*=\s*"([^"]+)"')

def base_tag_sets(bases_dir: str) -> dict:
    """Map item 'type' (e.g. 'Dagger') -> set of tags it carries."""
    import os
    out: dict[str, set] = {}
    for fn in os.listdir(bases_dir):
        if not fn.endswith(".lua"):
            continue
        with open(os.path.join(bases_dir, fn), encoding="utf-8", errors="ignore") as fh:
            cur_type = None
            for line in fh:
                tm = _TYPE_RE.search(line)
                if tm and "itemBases" in line or (tm and line.strip().startswith("type")):
                    cur_type = tm.group(1)
                gm = _TAGS_RE.search(line)
                if gm and cur_type:
                    tags = {t.strip() for t in gm.group(1).split(",")
                            if "=" in t and t.split("=")[1].strip() == "true"
                            for t in [t.split("=")[0]]}
                    out.setdefault(cur_type, set()).update(tags)
    return out


def pool_for_base(mods: list[Mod], base_tags) -> list[Mod]:
    """All mods that can roll on a base, given its TAG set (list/iterable).

    Accepts either a tag iterable (preferred) or a single token string (legacy).
    """
    if isinstance(base_tags, str):
        tags = [base_tags]
    else:
        tags = list(base_tags)
    return [m for m in mods if m.weight_for_tags(tags) > 0]


def dump_pool(mods: list[Mod], base_tags, out_path: str, base_label: str = None) -> int:
    if isinstance(base_tags, str):
        tags = [base_tags]
    else:
        tags = list(base_tags)
    pool = pool_for_base(mods, tags)
    rows = []
    for m in pool:
        d = asdict(m)
        d["weight"] = m.weight_for_tags(tags)    # resolved weight for this base
        d.pop("weights"); d.pop("_ordered_weights", None)
        d["source"] = m.source
        rows.append(d)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"base": base_label or (tags[0] if tags else "?"),
                   "tags": tags, "count": len(rows), "mods": rows}, fh, indent=2)
    return len(rows)


if __name__ == "__main__":
    # usage: python pob_loader.py <ModItem.lua> <BasesDir> <ItemType> <out.json>
    #   e.g. python pob_loader.py .../ModItem.lua .../Bases Dagger data/dagger_mods.json
    src, bases_dir, item_type, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    mods = parse_modfile(src)
    tagmap = base_tag_sets(bases_dir)
    tags = tagmap.get(item_type)
    if not tags:
        print(f"WARNING: no tags found for type '{item_type}'. "
              f"Known types sample: {sorted(tagmap)[:10]}")
        tags = {item_type.lower()}
    n = dump_pool(mods, tags, out, base_label=item_type)
    print(f"parsed {len(mods)} mods; {n} roll on '{item_type}' "
          f"(tags={sorted(tags)}) -> {out}")
