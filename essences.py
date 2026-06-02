"""
essences.py
Parse PoB Essence.lua into essence -> guaranteed-mod mappings.

Each essence forces a SPECIFIC mod onto an item, keyed by item CLASS name
(e.g. "Dagger", "One Hand Sword"). Three tiers exist per essence type:
  Lesser  (tierLevel ~12-25, mod tier 2-3)
  normal  (tierLevel 40,      mod tier 5-6)
  Greater (tierLevel 60,      mod tier 7-8)

0.5 mechanic (from patch coverage, flagged VERIFY): an essence applied to a
Normal item makes it Magic with the forced mod guaranteed. Essences count as a
"crafted modifier" in 0.5, so they occupy the single crafted-mod slot and
conflict with Alloy. We model only the guaranteed-mod-add behavior here; the
crafted-slot accounting is noted for the solver.

NOTE the class key is the display NAME ("Dagger"), which differs from the mod
loader's base token. The solver bridges them via the base's item 'type'.
"""
from __future__ import annotations
import re

_ENTRY = re.compile(r'\["Metadata/Items/Currency/(\w+)"\]\s*=\s*\{(.*?)\},?\s*$')
_NAME  = re.compile(r'name\s*=\s*"([^"]+)"')
_TYPE  = re.compile(r'type\s*=\s*"([^"]+)"')
_TIER  = re.compile(r'tierLevel\s*=\s*(\d+)')
_MODS  = re.compile(r'mods\s*=\s*\{(.*)\}\s*,?\s*$')
_KV    = re.compile(r'\["([^"]+)"\]\s*=\s*"([^"]+)"')


class Essence:
    __slots__ = ("key", "name", "etype", "tier_level", "mods")
    def __init__(self, key, name, etype, tier_level, mods):
        self.key = key            # e.g. CurrencyGreaterEssenceFire
        self.name = name          # e.g. "Greater Essence of Flames"
        self.etype = etype        # e.g. "Fire"
        self.tier_level = tier_level
        self.mods = mods          # {class_name: forced_mod_id}

    def forced_mod(self, class_name: str):
        return self.mods.get(class_name)

    def __repr__(self):
        return f"<Essence {self.name} (L{self.tier_level})>"


def parse_essences(path: str) -> list[Essence]:
    out = []
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            m = _ENTRY.search(line.rstrip())
            if not m:
                continue
            key, body = m.group(1), m.group(2)
            nm = _NAME.search(body); ty = _TYPE.search(body); tl = _TIER.search(body)
            mb = _MODS.search(body)
            mods = {}
            if mb:
                mods = {k: v for k, v in _KV.findall(mb.group(1))}
            out.append(Essence(
                key=key,
                name=nm.group(1) if nm else key,
                etype=ty.group(1) if ty else "?",
                tier_level=int(tl.group(1)) if tl else 0,
                mods=mods,
            ))
    return out


def essences_for_class(essences: list[Essence], class_name: str) -> list[Essence]:
    """Essences that can force a mod on the given item class, with the mod present."""
    return [e for e in essences if class_name in e.mods]


if __name__ == "__main__":
    import sys
    ess = parse_essences(sys.argv[1] if len(sys.argv) > 1
                         else "/home/claude/pob2/src/Data/Essence.lua")
    cls = sys.argv[2] if len(sys.argv) > 2 else "Dagger"
    rel = essences_for_class(ess, cls)
    print(f"{len(ess)} essences parsed; {len(rel)} apply to '{cls}':\n")
    for e in sorted(rel, key=lambda e: (e.etype, e.tier_level)):
        print(f"  {e.name:<34} L{e.tier_level:<3} -> {e.forced_mod(cls)}")
