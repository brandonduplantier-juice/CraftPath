"""
item_parser.py; parse a copied PoE2 item (in-game Ctrl+C or trade format) into
a starting-item spec CraftPath can use.

HONESTY:
  - The pasted text gives displayed mod lines with rolled values, plus rarity,
    item class, and item level. It does NOT label mods as prefix/suffix; we
    infer that by matching each line to a mod in CraftPath's pool and reading
    that mod's affix_type. So prefix/suffix is only as good as the match.
  - Matching is best-effort: we normalize numbers out and compare text patterns.
    Lines we cannot confidently match are returned in `unmatched` so the caller
    can flag them to the user and log them for later data correction.
  - Implicit mods, corrupted/crafted/unique mods, and mods from mechanics
    CraftPath doesn't model will typically not match; that's expected.
"""
from __future__ import annotations
import re

# normalize a mod line: lowercase, strip rolled numbers/ranges to a placeholder
_NUM = re.compile(r'[+\-]?\d+(?:\.\d+)?')
_RANGE = re.compile(r'\((\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\)')
# advanced copy format glues the rolled value to its range: "163(155-169)",
# "97(73-97)", "8.62(8-8.9)". Collapse the whole token to one placeholder.
_VAL_RANGE = re.compile(r'\d+(?:\.\d+)?\((\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\)')

def _first_number(text: str):
    # first standalone number in a line, used to pick the right tier.
    # In advanced format "163(155-169)" the FIRST number is the rolled value.
    m = re.search(r'(\d+(?:\.\d+)?)', text)
    return float(m.group(1)) if m else None

def _norm(text: str) -> str:
    t = text.strip().lower()
    t = _VAL_RANGE.sub('#', t)      # "163(155-169)" -> "#"  (advanced format)
    t = _RANGE.sub('#', t)          # "(5-8)" -> "#"
    t = _NUM.sub('#', t)            # "+7" / "42" -> "#"
    # collapse any sign that survived in front of the placeholder so pasted
    # "+22" and pool "+(5-8)" normalize identically
    t = re.sub(r'[+\-]\s*#', '#', t)
    t = t.replace('+#', '#').replace('-#', '#')
    # collapse repeated placeholders ("# #" or "##") from multi-number rolls
    t = re.sub(r'#[\s#]*#', '#', t)
    t = re.sub(r'\s+', ' ', t)
    t = t.strip(' .')
    return t

# rarity line variants from both formats
_RARITY_RE = re.compile(r'^\s*rarity:\s*(normal|magic|rare|unique)\s*$', re.I)
_ILVL_RE = re.compile(r'item\s*level:\s*(\d+)', re.I)


def _attr_suffix(has_ar, has_ev, has_es):
    # STR=Armour, DEX=Evasion, INT=Energy Shield; tokens use order str,dex,int
    parts = []
    if has_ar: parts.append("str")
    if has_ev: parts.append("dex")
    if has_es: parts.append("int")
    return "_".join(parts) if parts else None

_ARMOUR_SLOTS = {
    "gloves": "gloves", "glove": "gloves",
    "boots": "boots", "boot": "boots",
    "helmet": "helmet", "helm": "helmet", "helmets": "helmet",
    "body armour": "body", "body armours": "body",
    "shield": "shield", "shields": "shield",
}
_DIRECT_CLASS = {
    "amulet": "amulet", "amulets": "amulet", "belt": "belt", "belts": "belt",
    "ring": "ring", "rings": "ring", "quiver": "quiver", "quivers": "quiver",
    "focus": "focus", "foci": "focus", "talisman": "talisman", "talismans": "talisman",
    "claw": "claw", "claws": "claw", "dagger": "dagger", "daggers": "dagger",
    "flail": "flail", "flails": "flail", "spear": "spear", "spears": "spear",
    "bow": "bow", "bows": "bow", "crossbow": "crossbow", "crossbows": "crossbow",
    "staff": "staff", "staves": "staff", "sceptre": "sceptre", "sceptres": "sceptre",
    "quarterstaff": "quarterstaff", "quarterstaves": "quarterstaff",
    "wand": "wand", "wands": "wand",
    "one hand axe": "one_hand_axe", "one hand mace": "one_hand_mace",
    "one hand sword": "one_hand_sword", "two hand axe": "two_hand_axe",
    "two hand mace": "two_hand_mace", "two hand sword": "two_hand_sword",
    "one hand axes": "one_hand_axe", "one hand maces": "one_hand_mace",
    "one hand swords": "one_hand_sword", "two hand axes": "two_hand_axe",
    "two hand maces": "two_hand_mace", "two hand swords": "two_hand_sword",
}

def detect_base(raw, valid_tokens):
    """Best-effort detect the CraftPath base token from pasted item text.
    Uses item-class / base-type name + defence stats to pick attribute variants.
    Returns a token in valid_tokens or None."""
    if not raw:
        return None
    low = raw.lower()
    lines = [l.strip() for l in low.replace('\r','').split('\n') if l.strip()]
    has_ar = 'armour' in low
    has_ev = 'evasion' in low
    has_es = 'energy shield' in low
    cls_line = next((l for l in lines if l.startswith('item class:')), None)
    cls = cls_line.split(':',1)[1].strip() if cls_line else None
    search_space = cls if cls else " ".join(lines[:6])
    for word, slot in _ARMOUR_SLOTS.items():
        if re.search(r'\b'+re.escape(word)+r'\b', search_space):
            suf = _attr_suffix(has_ar, has_ev, has_es)
            if suf:
                tok = f"{slot}_{suf}"
                if tok in valid_tokens:
                    return tok
            cand = next((t for t in valid_tokens if t.startswith(slot+"_")), None)
            if cand:
                return cand
    for word, tok in _DIRECT_CLASS.items():
        if re.search(r'\b'+re.escape(word)+r'\b', search_space) and tok in valid_tokens:
            return tok
    # known item classes CraftPath has no data for yet; return a sentinel so the
    # caller can tell the user honestly rather than silently mismatching.
    # (quarterstaff is now supported via warstaff-tagged pool; no sentinel needed.)
    return None


def parse_item(raw: str, pool_mods, base_token=None):
    """
    raw: the pasted item text.
    pool_mods: list of mod objects/dicts with .text (list), .affix_type, .mod_id, .group
    base_token: optional CraftPath base token (used to identify jewellery, which
                always carries an implicit).
    returns dict: {rarity, item_level, matched:[{mod_id,affix_type,text,line}],
                   implicits:[...], unmatched:[lines], sections_seen:int, ok:bool, note}
    """
    if not raw or not raw.strip():
        return {"ok": False, "note": "empty paste"}

    # build a normalized lookup from the pool: norm_text -> list of (mod_id, affix_type, canonical_text)
    lookup = {}
    # Multi-line (hybrid) mods occupy ONE affix slot but display as 2+ lines
    # (e.g. increased Physical Damage + Accuracy Rating). Index them by the
    # tuple of their normalized lines so we can match them as a single affix
    # instead of counting each line as its own mod.
    multi_sigs = {}  # sig tuple -> list of (mid, aff, [canon texts], [(lo,hi)|None per line])
    for m in pool_mods:
        texts = m["text"] if isinstance(m, dict) else getattr(m, "text", None)
        if not texts:
            continue
        mid = m["mod_id"] if isinstance(m, dict) else m.mod_id
        aff = m["affix_type"] if isinstance(m, dict) else m.affix_type
        for t in texts:
            lookup.setdefault(_norm(t), []).append((mid, aff, t))
        if len(texts) > 1:
            sig = tuple(_norm(t) for t in texts)
            ranges = []
            for t in texts:
                rng = _RANGE.search(t)
                ranges.append((float(rng.group(1)), float(rng.group(2))) if rng else None)
            multi_sigs.setdefault(sig, []).append((mid, aff, list(texts), ranges))

    lines = [ln.rstrip() for ln in raw.replace('\r', '').split('\n')]
    rarity = None
    item_level = None
    # both formats use "--------" divider lines between sections
    # explicit mods are generally after the last divider that follows requirements
    matched, unmatched = [], []
    mod_lines = []   # candidate mod lines, matched after the loop (see below)
    mod_blocks = []  # divider-block index for each mod line (implicit vs explicit)
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
        # skip obvious non-mod metadata lines (strip leading space first!)
        low = ln.strip().lower()
        if low.startswith('{'):
            continue  # advanced-format annotation: { Prefix Modifier ... }
        # any line carrying a profile URL or markdown account link is trade cruft
        if 'pathofexile.com/account' in low or 'view-profile' in low or '](http' in low:
            continue
        if any(low.startswith(p) for p in (
            "item class:", "rarity:", "requirements:", "requires:", "level:",
            "str:", "dex:", "int:", "sockets:", "item level:", "quality:",
            "armour:", "evasion:", "energy shield:", "ward:", "{", "note:",
            "price ", "corrupted", "unidentified", "~price", "~b/o", "~", "exact price",
            "listed ", "online", "offline", "ign:",
            # trade listing cruft
            "asking price", "acc:", "account:", "fee:", "buyout", "b/o",
            "verified", "travel to", "ignore player", "whisper", "showing ",
            # weapon properties (not mods)
            "physical damage:", "elemental damage:", "chaos damage:",
            "critical hit chance:", "critical strike chance:", "attacks per second:",
            "weapon range:", "reload time:", "spirit:", "block chance:", "stack size:",
            "radius:", "limited to:", "tags:", "allocates", "grants skill")):
            continue
        # "listed N minutes/hours/days ago" anywhere in the line
        if re.search(r'listed .* ago', low) or re.search(r'\d+ (minute|hour|day|second)s? ago', low):
            continue
        # runic enchant lines copied as "... (rune)" / "... (implicit)" / "(crafted)"
        if low.endswith('(rune)') or low.endswith('(implicit)') \
           or low.endswith('(crafted)') or low.endswith('(enchant)'):
            continue
        # trade-site stat junk like "Armour16Energy Shield7" (concatenated stats,
        # no spaces between word and number) - not a real mod line
        if re.match(r'^(armour|evasion|energy\s*shield|ward)\d', low.replace(' ', '')):
            continue
        if re.match(r'^\s*(armour|evasion|energy shield|ward)\d+', low):
            continue
        # "X at max Quality: N" trade display lines
        if 'at max quality' in low:
            continue
        # weapon DPS display readouts: "DPS37.7", "Physical DPS12.33",
        # "Elemental DPS25.38", "DPS: 37.7"; derived stats, never mods.
        if re.match(r'^(physical |elemental |chaos |total )?dps[:\s]*[\d.]+$', low):
            continue
        if 'dps' in low and re.search(r'dps[:\s]*[\d.]+', low) and len(low) < 30:
            continue
        # collect this as a candidate mod line; matching happens after the loop
        # so multi-line (hybrid) mods can be matched as a single affix instead
        # of once per displayed line.
        mod_lines.append(ln.strip())
        mod_blocks.append(seen_divider)

    # --- match collected mod lines (multi-line hybrids first) ---
    norm_lines = [_norm(x) for x in mod_lines]
    vals = [_first_number(x) for x in mod_lines]
    i = 0
    while i < len(mod_lines):
        hit = False
        # try to consume consecutive lines as ONE hybrid affix. Only collapse
        # when every line's rolled value fits the SAME hybrid tier, so two
        # genuinely separate mods on a Rare are not merged by accident.
        for L in (3, 2):
            if i + L > len(mod_lines):
                continue
            sig = tuple(norm_lines[i:i + L])
            for mid, aff, canon, ranges in multi_sigs.get(sig, []):
                ok = True
                for k in range(L):
                    rng, v = ranges[k], vals[i + k]
                    if rng is not None and (v is None or not (rng[0] <= v <= rng[1])):
                        ok = False
                        break
                if ok:
                    matched.append({"mod_id": mid, "affix_type": aff,
                                    "text": " / ".join(canon),
                                    "line": " / ".join(mod_lines[i:i + L]),
                                    "ambiguous": False, "hybrid": True,
                                    "block": mod_blocks[i]})
                    i += L
                    hit = True
                    break
            if hit:
                break
        if hit:
            continue
        # single-line match
        ln = mod_lines[i]
        key = norm_lines[i]
        if key in lookup:
            cands = lookup[key]
            # disambiguate tier by the rolled value when possible: pick the tier
            # whose (lo-hi) range contains the pasted number.
            rolled = vals[i]
            chosen = None
            if rolled is not None and len(cands) > 1:
                for mid, aff, canon in cands:
                    rng = _RANGE.search(canon)
                    if rng and float(rng.group(1)) <= rolled <= float(rng.group(2)):
                        chosen = (mid, aff, canon)
                        break
            if chosen is None:
                chosen = cands[0]
            mid, aff, canon = chosen
            ambiguous = len({c[1] for c in cands}) > 1  # spans both affix types
            matched.append({"mod_id": mid, "affix_type": aff, "text": canon,
                            "line": ln, "ambiguous": ambiguous,
                            "block": mod_blocks[i]})
        else:
            # only treat plausible mod lines as unmatched (has a number or %),
            # to avoid flagging flavor text / names
            if _NUM.search(ln) or '%' in ln:
                unmatched.append(ln)
        i += 1

    # --- separate implicit (intrinsic base) mods from explicit affixes ---
    # PoE2 rules: implicits sit ABOVE the explicit mods (separated by a divider),
    # they are intrinsic to the base, and a Normal item has NO explicit mods. So:
    #   (a) if rarity is stated Normal, every matched line is an implicit;
    #   (b) any matched mod in an earlier divider block than the last mod block is
    #       an implicit (the explicit affixes are the last mod block);
    #   (c) jewellery always has an implicit, so a single lone matched mod on a
    #       jewellery base of unknown rarity (with nothing unmatched) is that
    #       implicit, not an affix.
    implicits = []
    if matched:
        for m in matched:
            m.setdefault("block", 0)
        last_block = max(m["block"] for m in matched)
        bt = (base_token or "").split("_")[0]
        is_jewellery = bt in ("amulet", "ring", "belt")
        explicit = []
        for m in matched:
            if rarity == "Normal" or m["block"] < last_block:
                implicits.append(m)
            else:
                explicit.append(m)
        if (rarity is None and is_jewellery and len(explicit) == 1
                and not implicits and not unmatched):
            implicits, explicit = explicit, []
        matched = explicit
        for m in matched + implicits:
            m.pop("block", None)

    # If the format had no explicit Rarity line (common in trade copies), infer
    # the MINIMUM rarity that fits the matched mods: 0 mods => Normal, fits
    # 1 prefix + 1 suffix => Magic, otherwise Rare. (app.py still auto-promotes
    # from kept/junk counts, so this is only the starting dropdown value.) If any
    # mod line failed to match, stay conservative with Rare, since the item may
    # carry more affixes than we resolved.
    if rarity is None:
        n_pre = sum(1 for m in matched if m["affix_type"] == "Prefix")
        n_suf = sum(1 for m in matched if m["affix_type"] == "Suffix")
        if n_pre == 0 and n_suf == 0 and not unmatched:
            rarity = "Normal"  # no mods at all => white base
        elif n_pre <= 1 and n_suf <= 1 and not unmatched:
            rarity = "Magic"   # one prefix + one suffix fits Magic
        else:
            rarity = "Rare"
        rarity_inferred = True
    else:
        rarity_inferred = False

    return {
        "ok": True,
        "rarity": rarity,
        "rarity_inferred": rarity_inferred,
        "item_level": item_level,
        "matched": matched,
        "implicits": implicits,
        "unmatched": unmatched,
        "n_matched": len(matched),
        "n_implicit": len(implicits),
        "n_unmatched": len(unmatched),
        "note": "Prefix/suffix inferred from CraftPath's mod pool via text match. "
                "Unmatched lines need manual entry (implicits, crafted, unique, or "
                "mods not yet in the pool).",
    }
