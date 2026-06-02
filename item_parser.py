"""
item_parser.py — parse a copied PoE2 item (in-game Ctrl+C or trade format) into
a starting-item spec CraftPath can use.

HONESTY:
  - The pasted text gives displayed mod lines with rolled values, plus rarity,
    item class, and item level. It does NOT label mods as prefix/suffix — we
    infer that by matching each line to a mod in CraftPath's pool and reading
    that mod's affix_type. So prefix/suffix is only as good as the match.
  - Matching is best-effort: we normalize numbers out and compare text patterns.
    Lines we cannot confidently match are returned in `unmatched` so the caller
    can flag them to the user and log them for later data correction.
  - Implicit mods, corrupted/crafted/unique mods, and mods from mechanics
    CraftPath doesn't model will typically not match — that's expected.
"""
from __future__ import annotations
import re

# normalize a mod line: lowercase, strip rolled numbers/ranges to a placeholder
_NUM = re.compile(r'[+\-]?\d+(?:\.\d+)?')
_RANGE = re.compile(r'\((\d+)-(\d+)\)')

def _first_number(text: str):
    # first standalone number in a line, used to pick the right tier
    m = re.search(r'(\d+(?:\.\d+)?)', text)
    return float(m.group(1)) if m else None

def _norm(text: str) -> str:
    t = text.strip().lower()
    t = _RANGE.sub('#', t)          # "(5-8)" -> "#"
    t = _NUM.sub('#', t)            # "+7" / "42" -> "#"
    # collapse any sign that survived in front of the placeholder so pasted
    # "+22" and pool "+(5-8)" normalize identically
    t = re.sub(r'[+\-]\s*#', '#', t)
    t = t.replace('+#', '#').replace('-#', '#')
    t = re.sub(r'\s+', ' ', t)
    t = t.strip(' .')
    return t

# rarity line variants from both formats
_RARITY_RE = re.compile(r'^\s*rarity:\s*(normal|magic|rare|unique)\s*$', re.I)
_ILVL_RE = re.compile(r'item\s*level:\s*(\d+)', re.I)


def parse_item(raw: str, pool_mods):
    """
    raw: the pasted item text.
    pool_mods: list of mod objects/dicts with .text (list), .affix_type, .mod_id, .group
    returns dict: {rarity, item_level, matched:[{mod_id,affix_type,text,line}],
                   unmatched:[lines], sections_seen:int, ok:bool, note}
    """
    if not raw or not raw.strip():
        return {"ok": False, "note": "empty paste"}

    # build a normalized lookup from the pool: norm_text -> list of (mod_id, affix_type, canonical_text)
    lookup = {}
    for m in pool_mods:
        texts = m["text"] if isinstance(m, dict) else getattr(m, "text", None)
        if not texts:
            continue
        mid = m["mod_id"] if isinstance(m, dict) else m.mod_id
        aff = m["affix_type"] if isinstance(m, dict) else m.affix_type
        for t in texts:
            lookup.setdefault(_norm(t), []).append((mid, aff, t))

    lines = [ln.rstrip() for ln in raw.replace('\r', '').split('\n')]
    rarity = None
    item_level = None
    # both formats use "--------" divider lines between sections
    # explicit mods are generally after the last divider that follows requirements
    matched, unmatched = [], []
    seen_divider = 0
    for ln in lines:
        if not ln.strip():
            continue
        if set(ln.strip()) == {'-'}:
            seen_divider += 1
            continue
        rm = _RARITY_RE.match(ln)
        if rm:
            rarity = rm.group(1).capitalize()
            continue
        il = _ILVL_RE.search(ln)
        if il:
            item_level = int(il.group(1))
            continue
        # skip obvious non-mod metadata lines
        low = ln.lower()
        if any(low.startswith(p) for p in (
            "item class:", "rarity:", "requirements:", "level:", "str:", "dex:",
            "int:", "sockets:", "item level:", "quality:", "armour:", "evasion:",
            "energy shield:", "ward:", "{ ", "note:", "price ", "corrupted",
            "unidentified")):
            continue
        # try to match this line as a mod
        key = _norm(ln)
        if key in lookup:
            cands = lookup[key]
            # disambiguate tier by the rolled value when possible: pick the tier
            # whose (lo-hi) range contains the pasted number.
            rolled = _first_number(ln)
            chosen = None
            if rolled is not None and len(cands) > 1:
                for mid, aff, canon in cands:
                    rng = _RANGE.search(canon)
                    if rng:
                        lo, hi = int(rng.group(1)), int(rng.group(2))
                        if lo <= rolled <= hi:
                            chosen = (mid, aff, canon); break
            if chosen is None:
                chosen = cands[0]
            mid, aff, canon = chosen
            ambiguous = len({c[1] for c in cands}) > 1  # spans both affix types
            matched.append({"mod_id": mid, "affix_type": aff, "text": canon,
                            "line": ln.strip(), "ambiguous": ambiguous})
        else:
            # only treat plausible mod lines as unmatched (has a number or %),
            # to avoid flagging flavor text / names
            if _NUM.search(ln) or '%' in ln:
                unmatched.append(ln.strip())

    return {
        "ok": True,
        "rarity": rarity,
        "item_level": item_level,
        "matched": matched,
        "unmatched": unmatched,
        "n_matched": len(matched),
        "n_unmatched": len(unmatched),
        "note": "Prefix/suffix inferred from CraftPath's mod pool via text match. "
                "Unmatched lines need manual entry (implicits, crafted, unique, or "
                "mods not yet in the pool).",
    }
