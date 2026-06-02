# Adding real (Craft of Exile) weights for a base

Real spawn weights do NOT exist in PoE2's game files. Confirmed by both
Path of Building and PoE2DB, which each state this and show flat weights.
Craft of Exile is the only source that estimates weights (from trade-listing
analysis). So weights are added per base, by reading them off CoE.

The app works for all 51 bases right now with the mod POOL, tiers, ilvl gates,
groups, and essences fully correct. Bases without CoE weights use flat weights
and the UI badge shows "PLACEHOLDER (FLAT)". The dagger has real CoE weights and
shows "CoE estimate"; its odds match CoE exactly (fire prefix 14.286%).

## To add weights for a base

1. On craftofexile.com/?game=poe2, select the base. Read the PREFIXES and
   SUFFIXES tables — each mod family shows a Weight column (e.g. Fire Damage = 10).
2. Add an entry to data/coe_weights.json keyed by the base token, mapping each
   PoB mod GROUP to its CoE family weight. The generator script
   `add_coe_weights.py` (below) does the group->mod_id expansion for you:
   you supply {group_name: weight} and it fills every tier.
3. Restart the app. The badge flips to "CoE estimate" for that base and the
   odds become accurate.

The dagger entry in coe_weights.json is the worked example to copy.
