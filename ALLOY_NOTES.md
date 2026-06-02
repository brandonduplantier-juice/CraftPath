# Alloy Crafting (PoE2 0.5 Runes of Aldur) - implementation notes

Status: MECHANICS VERIFIED, per-alloy modifiers NOT YET DATAMINED.
The app currently lists alloys as unsupported-for-odds but documents the system.

## Verified mechanics (multiple independent sources, see below)
- 13 alloy types total.
- Each alloy REMOVES one random existing modifier from a RARE item and ADDS one
  specific guaranteed modifier (unavailable through normal crafting).
- Works on RARE items only - not normal, magic, or unique.
- Behaves like Perfect Essences: the added mod occupies the SINGLE crafted-mod
  slot, and COMPETES with essences (one crafted mod per item, whether from an
  essence or an alloy - you cannot have both).
- Lands as a guaranteed prefix or suffix (depending on the alloy's mod type).
- Unlocked after Farrow's Act 2 quests via Remnant encounters.
- Stackable and tradable.
- Crafted at the Verisium Anvil; obtained from Remnant encounters / higher rune nodes.
- Tiering observed: ~6 cheaper alloys, ~6-7 more expensive ones (Prismatic,
  Mystic, Transcendent, Celestial, Sovereign, etc.). Prices NOT recorded here -
  they are league-volatile and were already shifting day 4.

## What's still MISSING for the solver to model alloys
1. The specific guaranteed modifier each of the 13 alloys grants.
2. Whether the granted mod's VALUE rolls a range or is fixed.
3. Which affix slot (prefix/suffix) each alloy targets.
Without (1)-(3) there are no odds to compute - an alloy is a deterministic
"replace random mod with mod X" operation once X is known per alloy.

## How to implement once data exists
Alloys are simpler than the solver's probabilistic actions: each is a
deterministic craft (guaranteed result), so model each as a named action that
forces its specific mod (like essences already do via forced_mod). Add a
data/alloys.json mapping alloy_name -> {mod_id, affix, value_range}, and treat
it in the same code path as essences (they share the crafted slot anyway).

## Sources (fetched 2026-06-02)
- PoE2 Wiki (Fextralife): Runeforging Guide
- Maxroll.gg: Runes of Aldur Overview
- Game8: Runeforging Crafting Mechanics
- BugFreeGG: 0.5.0 Crafting Changes ("13 of them... specific modifiers not yet
  fully documented... expected strong finishing tool for partial crafts")
- aoeah: Verisium Anvil Currency guide ("Alloys only work on rare equipment")
- boostmatch.gg: 0.5 complete guide ("13 new types... removes a random property
  ... adds a specific guaranteed property unavailable through normal means")

NOTE: Runeforging (Verisium -> Runic Ward on armour, and unique-upgrade) is a
SEPARATE system from alloys and is a defensive/unique-base mechanic, not a
random-mod craft - out of scope for the affix optimizer.
