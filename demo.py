"""
demo.py ; end-to-end sanity check on real PoE2 0.5 claw data.

Scenario: an ilvl 81 claw, already Rare with two prefixes filled. We want a
specific high suffix and ask: if we Exalt (add one random eligible mod), what's
the chance we hit it, and how many Exalts on average?
"""
import json
from item_state import ItemState, RolledAffix
from probability import estimate_step, addable_pool

with open("data/claw_mods.json") as fh:
    raw = json.load(fh)["mods"]

# rebuild lightweight Mod-like objects for the engine
class M:
    def __init__(self, d): self.__dict__.update(d)
    def weight_for(self, _): return self.weight
mods = [M(d) for d in raw]

# pick a concrete target: the top-tier attack-speed suffix available
suffixes = sorted([m for m in mods if m.affix_type == "Suffix"],
                  key=lambda m: m.level)
target = next((m for m in suffixes if "AttackSpeed" in m.mod_id
               or "attack speed" in (m.text[0].lower() if m.text else "")), suffixes[-1])

# state: ilvl 81 rare claw with 2 prefixes already locked (suffixes wide open)
state = ItemState(
    base_token="claw", item_level=81, rarity="Rare",
    affixes=[
        RolledAffix("LocalAddedFireDamage8", "Prefix", "LocalFireDamage"),
        RolledAffix("LocalPhysicalDamagePercent5", "Prefix", "LocalPhysicalDamagePercent"),
    ],
)

pool = addable_pool(state, mods)
print(f"Target mod: {target.mod_id}  ({target.text[0] if target.text else ''})")
print(f"Item: ilvl {state.item_level} {state.rarity} claw | "
      f"open prefixes {state.open_pre()}, open suffixes {state.open_suf()}")
print(f"Eligible mods an Exalt could add right now: {len(pool)} "
      f"(total weight {sum(m.weight for m in pool)})")
print()
print(estimate_step(state, mods, target.mod_id))

# show the full eligible suffix pool so the number is auditable
print("\nEligible suffixes (weight gates the odds):")
for m in sorted([m for m in pool if m.affix_type == "Suffix"], key=lambda m: m.mod_id):
    print(f"  {m.mod_id:<26} w={m.weight:<3} L{m.level:<3} {m.text[0] if m.text else ''}")
