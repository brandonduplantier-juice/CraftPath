"""
methods.py
Crafting currencies modeled as state transitions over an ItemState.

Each method declares: when it's usable, what it does, and (for adding mods)
which pool it draws from so probability.py can score the step.

Confidence flags reflect how sure I am of the 0.5 behavior:
  CONFIRMED  - verified against 0.5 patch notes / community sources
  STANDARD   - unchanged long-standing PoE behavior, high confidence
  VERIFY     - behavior may have changed in 0.5; do not trust blindly
  UNVERIFIED - 0.5 mechanic with no reliable probability data yet (runes)
"""
from __future__ import annotations
from dataclasses import dataclass
from item_state import ItemState

ADD = "add_random"      # adds one random eligible mod
REROLL = "reroll_vals"  # rerolls numeric values, mod set unchanged
REMOVE = "remove_random"
FORCE = "force_mod"     # adds a specific predetermined mod (essences)


@dataclass(frozen=True)
class Method:
    name: str
    action: str
    requires_rarity: tuple          # rarities it can be used on
    sets_rarity: str | None         # rarity after use (None = unchanged)
    needs_open_slot: bool
    confidence: str
    note: str = ""


# --- core orbs (the quote in 0.5 coverage confirms transmute/aug/regal) -----
TRANSMUTE = Method("Transmutation Orb", ADD, ("Normal",), "Magic", True, "CONFIRMED")
AUGMENT   = Method("Augmentation Orb",  ADD, ("Magic",),  None,    True, "CONFIRMED",
                   "adds a 2nd mod to a magic item")
REGAL     = Method("Regal Orb",         ADD, ("Magic",),  "Rare",  True, "CONFIRMED",
                   "magic -> rare, adds 1 mod")
EXALT     = Method("Exalted Orb",       ADD, ("Rare",),   None,    True, "STANDARD",
                   "adds 1 random mod to a rare with an open slot")
DIVINE    = Method("Divine Orb",        REROLL, ("Magic", "Rare"), None, False, "STANDARD",
                   "rerolls numeric rolls; does NOT change which mods are present")
ANNUL     = Method("Orb of Annulment",  REMOVE, ("Magic", "Rare"), False, "STANDARD",
                   "removes ONE random mod - cannot choose which")

# --- methods I'm deliberately not auto-scoring until verified ---------------
CHAOS     = Method("Chaos Orb", REMOVE, ("Rare",), None, False, "VERIFY",
                   "0.5 remove/add behavior not confirmed - left unscored")
ESSENCE   = Method("Essence", FORCE, ("Normal", "Magic"), "Magic", True, "VERIFY",
                   "forces a specific mod; needs Essence.lua mapping wired in")
RUNE_ALDUR = Method("Runes of Aldur", ADD, ("Normal", "Magic", "Rare"), None, True,
                    "UNVERIFIED", "0.5 league craft; probabilities not yet documented")

CORE_METHODS = [TRANSMUTE, AUGMENT, REGAL, EXALT, DIVINE, ANNUL]


# --- mechanics confirmed by 0.5 crafting demonstration (video transcript) ----
# These refine the action set. Tiered currencies bias toward higher mod TIERS
# (not different mods), so they change roll quality, not which mod is hit.
GREATER_AUGMENT = Method("Greater Orb of Augmentation", ADD, ("Magic",), None, True,
                         "CONFIRMED", "augment biased to higher mod tiers")
PERFECT_AUGMENT = Method("Perfect Orb of Augmentation", ADD, ("Magic",), None, True,
                         "CONFIRMED", "augment biased to highest mod tiers")
GREATER_EXALT   = Method("Greater Exalted Orb", ADD, ("Rare",), None, True,
                         "CONFIRMED", "exalt biased to higher mod tiers")
PERFECT_EXALT   = Method("Perfect Exalted Orb", ADD, ("Rare",), None, True,
                         "CONFIRMED", "exalt biased to highest mod tiers")

# Desecration (Well of Souls): a TWO-STEP craft.
#   1. Apply a Bone currency (Jawbone=weapon/quiver, Rib=armour, Collarbone=jewellery)
#      -> adds ONE *unrevealed* desecrated modifier (occupies a slot). If the item
#         is full, a random existing mod is removed first.
#   2. Reveal at the Well of Souls -> the unrevealed mod resolves. The reveal pool
#      MAY include normal base mods unless an Omen forces a named (lord) mod.
DESECRATE = Method("Desecrate (Bone)", ADD, ("Rare", "Magic", "Normal"), None, True,
                   "CONFIRMED", "adds 1 unrevealed desecrated mod; reveal at Well of Souls")

# Omens that modify the NEXT currency use (meta-craft equivalents in 0.5):
#   Sinistral Necromancy  -> next desecrate adds a PREFIX only
#   Dextral Necromancy    -> next desecrate adds a SUFFIX only
#   Abyssal Echoes        -> reroll the desecrated reveal options once (~99 ex)
#   the Sovereign/Liege/Blackblooded -> next desecrate guarantees Ulaman/Amanamu/Kurgal
#   Putrefaction          -> replace all mods, up to 6 unrevealed, and corrupt
#   Greater Exaltation    -> next Exalted Orb adds TWO random mods at once
#   Light                 -> next Annul removes only a desecrated mod
OMENS = {
    "Sinistral Necromancy": {"affects": "Desecrate", "effect": "prefix_only", "cost_hint": 9},
    "Dextral Necromancy":   {"affects": "Desecrate", "effect": "suffix_only"},
    "Abyssal Echoes":       {"affects": "Reveal",    "effect": "reroll_options", "cost_hint": 99},
    "the Sovereign":        {"affects": "Desecrate", "effect": "force_lord:ulaman"},
    "the Liege":            {"affects": "Desecrate", "effect": "force_lord:amanamu"},
    "the Blackblooded":     {"affects": "Desecrate", "effect": "force_lord:kurgal"},
    "Greater Exaltation":   {"affects": "Exalted Orb", "effect": "add_two"},
    "Light":                {"affects": "Orb of Annulment", "effect": "remove_desecrated_only"},
}

# Non-mod finishing steps (tracked for completeness; not part of mod optimization):
#   Quality (Whetstone/Blacksmith / Arcanist) raises DPS/defence, not mods.
#   Runes socketed into the item add enchantment mods (separate from affixes).
#   Veridian Anvil adds Ward to armour, but may DEPLETE armour/ES/evasion to do so
#     (notably on body armour; negligible on helmet/boots).
FINISHING = ["Quality (Whetstone/Arcanist)", "Runes (socket enchants)",
             "Veridian Anvil (Ward, may reduce defences)"]


# === further 0.5 mechanics confirmed across multiple crafting demonstrations ===

# TIERED CURRENCY = a MINIMUM MODIFIER LEVEL floor (not different mods).
# Greater/Perfect orbs cut off low tiers by raising the min mod level that can
# roll, so they bias outcomes toward higher tiers. Confirmed: "perfect orb of
# transmutation/augmentation rolls minimum modifier level 70". Modeled as a
# floor applied to the eligible pool before sampling.
CURRENCY_MIN_MOD_LEVEL = {
    "Orb of Transmutation": 1,  "Greater Orb of Transmutation": 35, "Perfect Orb of Transmutation": 70,
    "Augmentation Orb": 1,      "Greater Orb of Augmentation": 35,  "Perfect Orb of Augmentation": 70,
    "Regal Orb": 1,             "Greater Regal Orb": 35,            "Perfect Regal Orb": 70,
    "Exalted Orb": 1,           "Greater Exalted Orb": 35,          "Perfect Exalted Orb": 70,
}

# Two distinct OMEN families that direct WHERE the next currency acts:
#   *Exaltation* omens steer the next EXALTED ORB to prefix or suffix.
#   *Necromancy* omens steer the next DESECRATION to prefix or suffix.
#   *Crystallization* omens steer the next FRACTURE to prefix or suffix (ritual).
OMEN_EXALTATION = {
    "Sinistral Exaltation": "next Exalted Orb adds a PREFIX",
    "Dextral Exaltation":   "next Exalted Orb adds a SUFFIX",
    "Greater Exaltation":   "next Exalted Orb adds TWO random mods",
}
OMEN_CRYSTALLIZATION = {
    "Sinistral Crystallization": "next Fracture targets a PREFIX",
    "Dextral Crystallization":   "next Fracture targets a SUFFIX",
}

# PUTREFACTION: the dominant cheap-reliable craft. Omen of Putrefaction + Bone on
# a RARE (uncorrupted, undesecrated) replaces ALL mods with up to 6 UNREVEALED
# desecrated mods and corrupts the item. Reveal prefixes-then-suffixes at the
# Well of Souls, choosing among options per reveal (group-blocking applies).
PUTREFACTION = Method("Omen of Putrefaction + Bone", ADD, ("Rare",), None, True,
                      "CONFIRMED",
                      "replaces all mods with 6 unrevealed desecrated mods + corrupts; "
                      "reveal at Well of Souls. See putrefaction.py for odds.")

# FRACTURING ORB: permanently locks ONE mod (cannot be removed/changed). Drops
# from cleansed maps near a Corrupted Nexus (~4-5 div). Used to lock a good mod,
# then safely manipulate the rest. Modeled as: pick a secured mod -> fractured.
FRACTURE = Method("Fracturing Orb", "fracture", ("Rare",), None, False, "CONFIRMED",
                  "locks one existing mod permanently; rare currency")

# REFORGING BENCH: combine several junk items of the same type for a chance at a
# better-rolled item. A salvage/gamble path for failed crafts (no clean odds).
REFORGE = Method("Reforging Bench", ADD, ("Rare",), "Rare", True, "UNVERIFIED",
                 "combine junk same-type items for a re-roll chance; odds unpublished")

# ESSENCE placement rule (refined): only ONE essence (crafted mod) is allowed at
# a time in 0.5 (greater AND perfect essences both count as the single crafted
# mod). Perfect essences ALWAYS remove an existing mod and add their forced mod,
# which lands in its fixed prefix/suffix slot. This is why multi-essence stacking
# is gone and crafts are "one essence + one desecration" shaped.


def applicable(state: ItemState) -> list[Method]:
    """Methods usable on the item right now (core, scoreable ones only)."""
    out = []
    for m in CORE_METHODS:
        if state.rarity not in m.requires_rarity:
            continue
        if m.needs_open_slot and state.open_slots() <= 0:
            continue
        if m.action == REMOVE and len(state.affixes) == 0:
            continue
        out.append(m)
    return out
