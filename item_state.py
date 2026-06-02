"""
item_state.py
Models a single item's craftable state for PoE2.

Slot rules (PoE2 0.5):
  Normal : 0 prefixes, 0 suffixes
  Magic  : up to 1 prefix, 1 suffix
  Rare   : up to 3 prefixes, 3 suffixes

A "group" can appear at most once on an item (mutual exclusion). The engine
uses this to decide what can still be added and to detect a brick: the target
needs a slot/group that is no longer reachable with the allowed methods.
"""
from __future__ import annotations
from dataclasses import dataclass, field

SLOTS = {"Normal": (0, 0), "Magic": (1, 1), "Rare": (3, 3)}


@dataclass(frozen=True)
class RolledAffix:
    mod_id: str
    affix_type: str   # "Prefix" | "Suffix"
    group: str
    crafted: bool = False   # occupies the single crafted-mod slot (0.5 rule)


@dataclass
class ItemState:
    base_token: str
    item_level: int
    rarity: str = "Normal"
    affixes: list[RolledAffix] = field(default_factory=list)

    # --- slot accounting -------------------------------------------------
    def max_pre(self) -> int: return SLOTS[self.rarity][0]
    def max_suf(self) -> int: return SLOTS[self.rarity][1]

    def n_pre(self) -> int:
        return sum(a.affix_type == "Prefix" for a in self.affixes)
    def n_suf(self) -> int:
        return sum(a.affix_type == "Suffix" for a in self.affixes)

    def open_pre(self) -> int: return self.max_pre() - self.n_pre()
    def open_suf(self) -> int: return self.max_suf() - self.n_suf()
    def open_slots(self) -> int: return self.open_pre() + self.open_suf()

    def filled_groups(self) -> set[str]:
        return {a.group for a in self.affixes}

    def has_crafted(self) -> bool:
        return any(a.crafted for a in self.affixes)

    # --- can a given mod be added right now? -----------------------------
    def can_add(self, mod) -> bool:
        if mod.level > self.item_level:
            return False
        if mod.group in self.filled_groups():
            return False
        if mod.affix_type == "Prefix" and self.open_pre() <= 0:
            return False
        if mod.affix_type == "Suffix" and self.open_suf() <= 0:
            return False
        return True

    def with_added(self, mod, crafted: bool = False) -> "ItemState":
        new = RolledAffix(mod.mod_id, mod.affix_type, mod.group, crafted)
        return ItemState(self.base_token, self.item_level, self.rarity,
                         self.affixes + [new])
