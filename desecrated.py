"""
desecrated.py
Parse PoE2DB "Desecrated Modifiers" text dumps into structured mods, and encode
the Well of Souls mechanics rules.

PoE2DB rows look like (tab- or newline-separated):
    Amanamu's   65   Prefix   (74-89)% increased Elemental Damage  amanamu Damage Elemental Fire Cold Lightning
    of Kurgal   65   Suffix   +(13-17)% to Cold and Chaos Resistances  kurgal Elemental Cold Chaos Resistance

Fields: affix_name (lord), ilvl, Prefix/Suffix, stat text(+tags trailing).
The lord is one of: ulaman / amanamu / kurgal (the Abyssal Lords), or
"Lightless" / "of the Abyss" for jewel-specific desecrated mods.

MECHANICS RULES (from PoE2DB notes, encoded as constraints):
  - A Desecration reveal MAY produce a normal base modifier, not only a
    desecrated one, unless an Omen forces a named (lord) modifier.
  - Body Armour, Gloves, Boots, Helmet have NO prefix desecrated mods
    (desecrated mods on those slots are suffix-only).
  - Sceptres have no exclusive desecrated mods.
  - Lich omens (force a specific lord) work only on Weapons and Jewellery.
  - Jewel exclusive desecrated mods don't roll on Time-Lost jewels.
"""
from __future__ import annotations
import re, json

LORDS = ("ulaman", "amanamu", "kurgal")

# slots that cannot receive PREFIX desecrated mods
NO_PREFIX_DESECRATED = {"body", "gloves", "boots", "helmet"}
# bases with no exclusive desecrated mods at all
NO_DESECRATED = {"sceptre"}
# lord-forcing omens only valid on these category kinds
LORD_OMEN_CATEGORIES = {"weapon", "jewellery"}


def _lord_of(affix_name: str, tag_blob: str) -> str:
    a = (affix_name + " " + tag_blob).lower()
    for l in LORDS:
        if l in a:
            return l
    if "abyss" in a or "lightless" in a:
        return "jewel_abyssal"
    return "unknown"


_ROW = re.compile(
    r"^(?P<name>(?:[A-Z][a-z]+(?:'s)?)|of\s+\w+|Lightless|of the Abyss)\s+"
    r"(?:(?P<ilvl>\d+)\s+)?(?P<slot>Prefix|Suffix)\s+(?P<rest>.+)$"
)


def parse_desecrated_dump(text: str) -> list[dict]:
    """Parse a pasted PoE2DB desecrated table into mod dicts."""
    mods = []
    # join wrapped stat lines: a row starts with a lord token; continuation
    # lines (second stat of a hybrid, or trailing tags) attach to the prior row.
    raw_lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    cur = None
    for ln in raw_lines:
        m = _ROW.match(ln.strip())
        if m:
            if cur:
                mods.append(cur)
            name = m.group("name").strip()
            rest = m.group("rest").strip()
            cur = {
                "affix_name": name,
                "ilvl": int(m.group("ilvl")) if m.group("ilvl") else 1,
                "affix_type": m.group("slot"),
                "text_lines": [rest],
                "lord": _lord_of(name, rest),
                "source": "desecrated",
            }
        elif cur is not None:
            # continuation (second hybrid stat or tag overflow)
            cur["text_lines"].append(ln.strip())
    if cur:
        mods.append(cur)

    # finalize: split trailing tag words from the last text line heuristically
    for d in mods:
        d["text"] = " / ".join(d.pop("text_lines"))
    return mods


def can_roll_desecrated(base_token: str, affix_type: str) -> bool:
    """Apply the slot rules to whether a desecrated mod of this type is allowed."""
    bt = base_token.split("_")[0]
    if bt in NO_DESECRATED:
        return False
    if affix_type == "Prefix" and bt in NO_PREFIX_DESECRATED:
        return False
    return True


def lord_omen_valid(base_token: str) -> bool:
    """Whether lord-forcing omens (guarantee an Ulaman/Amanamu/Kurgal mod) apply."""
    weapons = {"claw","dagger","flail","spear","bow","crossbow","staff","wand","sceptre",
               "one","two"}  # 'one_hand_*' / 'two_hand_*' start with one/two
    jewellery = {"amulet","ring","belt"}
    bt = base_token.split("_")[0]
    return bt in weapons or bt in jewellery


if __name__ == "__main__":
    import sys
    txt = open(sys.argv[1]).read() if len(sys.argv) > 1 else sys.stdin.read()
    mods = parse_desecrated_dump(txt)
    print(f"parsed {len(mods)} desecrated mods")
    from collections import Counter
    print("by lord:", dict(Counter(m["lord"] for m in mods)))
    print("by type:", dict(Counter(m["affix_type"] for m in mods)))
    for m in mods[:5]:
        print(f"  [{m['lord']}] {m['affix_type']} ilvl{m['ilvl']}: {m['text'][:60]}")
