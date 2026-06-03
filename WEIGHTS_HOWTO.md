# How to upgrade a base from estimated to real mod weights

## The honest status
- Mod POOL, groups, and item-level gating: reliable (from Path of Building data).
- Mod WEIGHTS (spawn probabilities within a group): NOT in the game client.
  - Only Craft of Exile publishes estimated weights, and only some bases.
  - dagger: uses real Craft of Exile weights (data/coe_weights.json). Badge: craft_of_exile_estimate.
  - all other bases: flat_uniform; every mod in a group weighted equally, with
    tier rarity handled by the solver's item-level gating, not weight decay.
    Badge: flat_uniform (the UI flags this honestly).

## Why flat_uniform is defensible
PoE2 weights are flat within a mod group (verified against dagger CoE data; all
tiers of a group share one weight). Tier rarity comes from ilvl gating: higher
tiers require higher item level, which the solver already filters on. So
flat_uniform is a reasonable approximation, not a fabrication; and it's labeled.

## To add real weights for a base
1. Get the base's weight table from Craft of Exile (their data, JS-locked, must be
   read manually; it isn't fetchable programmatically).
2. Add it to data/coe_weights.json keyed by base token, same structure as dagger.
3. The loader will pick it up and flip the badge to craft_of_exile_estimate.

Do NOT invent weights. If real data isn't available, leave it flat_uniform; the
whole tool's credibility rests on labeling estimated vs. verified honestly.
