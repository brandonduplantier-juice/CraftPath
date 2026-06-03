# CraftPath; Backlog / TODO

Running list of things to add or fix, captured as they come up so nothing gets lost.
Newest items near the top of each section. Status: ☐ todo · ◐ in progress · ☑ done.

## High impact
- ☑ **Per-base weights pipeline.** DONE via build_weights.py: pulls real per-base
  spawn weights from PoB ModItem.lua (weightKey/weightVal) for all bases and removes
  mods that can't roll on a base (weight 0). All bases now report weights_source
  'pob_real' (dagger keeps its richer CoE estimate). NOTE: PoB 0.5 weights are often
  flat (1) for weapon mods, so the big win is base-VALIDITY filtering, not weight
  spread. Re-run build_weights.py when PoB data updates. Source scripts live in repo.
  FOLLOW-UP DONE: build_pools.py regenerates the COMPLETE per-base pool from PoB
  (the original scrape was missing 1271 mods total; talisman 46->239 valid, wand
  105->276, sceptre 150->257, staff 196->276). Also surfaced 4 triple-attribute
  armour bases (body/boots/gloves/helmet _str_dex_int) that had 0 mods / didn't
  exist; now added to bases_index.json (56 bases total). dagger keeps CoE overlay.

## Monetization (later)
- ☐ **Ads.** Replicate poe.ninja's unobtrusive ads. poe.ninja uses Google AdSense
  (per Similarweb) and Playwire (seen in their page source). AdSense is the easy
  first move but needs Google approval, which depends on traffic; revenue is small
  until traffic is large. Gaming networks (AdinPlay/Venatus) pay better but gate on
  volume. Plan: AdSense top+sidebar slots when ready → Venatus once traffic grows.
  Slots can be built behind a config flag and stay off until approved.

## Functionality
- ☑ **Method-filter checkboxes (Step 2).** Essence / Greater-Perfect / Bones&Omens
  checkboxes above the search grey out mods a checked method can't make (popup explains
  why) + info tooltips. Checked methods CONSTRAIN the solver's plan (enabled_methods →
  basic orbs always + only checked optional methods; nothing checked = all). Bones &
  Omens enables the Exaltation/Annulment steering omens AND makes desecrated mods
  directly selectable as targets; clicking one adds a 🦴-marked chip; any desecrated
  target routes the plan to the Putrefaction (Well of Souls) how-to guide.
- ☑ **Decision-tree view.** Each probabilistic step now shows an explicit success/fail
  fork: "✓ hits (X%) → next step" / "✗ misses (Y%) → recovery action / brick", alongside
  the existing recovery line.
- ◐ **Niche omens.** DONE (verified mechanics): Sinistral/Dextral Coronation (next Regal
  adds only prefix/suffix) and Sinistral/Dextral Erasure (next Chaos removes only
  prefix/suffix then adds), both gated by the omens checkbox, with plan method-notes.
  Costs are flagged 5ex placeholders (Ritual-only omens, not reliably on currency
  market; prices.py doesn't fetch omens). Whittling added as GUIDANCE in the removal note (not a solver action; its 'lowest-LEVEL mod'
  targeting can't be tracked in solver state, so it's informational: 'if the unwanted mod is
  lowest-level, Whittling+Chaos removes exactly it').

## Data refresh (run locally; needs network the dev box lacks)
- ☑ **Desecrated base-type filtering.** DONE via build_desecrated.py: rebuilt
  data/desecrated_mods.json from PoB ModVeiled.lua (196 lord mods: ulaman/amanamu/
  kurgal) WITH real per-base weightKey tags. /api/desecrated and the solve pool now
  filter to mods that can actually roll on the base (e.g. quarterstaff shows 6pre/6suf
  weapon mods, not the old flat 70/125 with Charms/Spells/Curses). Tecrod/Kulemak NOT
  added; they're Timeless-Jewel lords, not Well-of-Souls reveal mods (verified).
- ☐ **Essence prices** seeded as TIERED ESTIMATES (Lesser~0.5 / Normal~2 / Greater~8
  / Perfect~25 ex). Run `python prices.py` locally to overwrite with live PoE2 Scout
  values. A few high-demand Greater essences really trade 50+ ex.
- ◐ **Currency prices** refresh with `python prices.py` LOCALLY (scout/ninja unreachable
  from build box). Script verified syntactically valid; fetches currency + essence
  prices. Does NOT fetch omens (coronation/erasure/exaltation use flagged placeholders).
  Tier-floor: Greater=44 (VERIFIED, 0.5 patch notes; was wrongly 35); Perfect=50 still estimate.

## Item art (deferred by Brandon)
- ◐ **Real item art.** Harvester written: run `python harvest_item_art.py` LOCALLY
  (poe2db/poecdn unreachable from the build box) -> writes data/item_art.json, served by
  /api/item-art and merged into BASE_ART at load. PoB has no per-base art paths, so the
  scraper reads poe2db category pages. First local run returned 0/57 (poe2db icons are hashed poecdn paths, often no .png ext, lazy-
  loaded). Harvester hardened (data-src + /image/ + /gen/ + raw-HTML regex fallback); needs
  another local run to confirm. If still 0, poe2db likely renders icons via JS; would need a
  different art source. Placeholders stay until resolved.

## Done this session
- ☑ Quarterstaff support (real warstaff-tagged pool from PoB; 70/88 mods).
- ☑ Essence prices seeded + fixed quarterstaff→Warstaff class mismatch so essences
  actually get chosen (was silently broken).
- ☑ Step 3 shows kept mods locked on the sides.
- ☑ Desecration prefix/suffix reference boxes in Step 2.
- ☑ Video-accurate desecration how-to (activate omen, bones, Well of Souls, Abyssal
  Echoes, post-reveal Greater/Perfect Exalt + Greater Exaltation omen).
- ☑ Safe vs unsafe annul guidance.
- ☑ Full currency coverage: Greater/Perfect tiered orbs, Alchemy, Chaos (0.5 remove-
  one-add-one), annul omens. Exact deterministic policy-iteration solver.
- ☑ Item-art slot + GGG credit (placeholder silhouettes).
- ☑ Viability gate counts only NEW mods, not kept ones.

## QA
- ☑ **Solver test harness (test_crafts.py).** Generates diverse crafts across all base
  categories × method sets × random tiers and checks for crashes, negative costs,
  non-convergence, absurd costs, empty plans, and unreachable goals. 1,620 crafts across
  3 seeds: ZERO real violations. Optimality spot-checks confirm smart path selection
  (essence when cheapest, slam when no essence reaches the tier, omens when enabled,
  graceful fallback under method constraints). Re-run anytime: python test_crafts.py [N].

## Putrefaction guide (tailored)
- ☑ **Craft-specific desecration guide + shopping list.** The putrefaction how-to is now
  tailored to the actual craft instead of generic boilerplate: names YOUR base ("use your
  Quarterstaff", correct bone = Jawbone/Rib/Collarbone by category), the SPECIFIC essence for
  your chosen non-desecrated mod (e.g. "apply Essence of Flames to guarantee your Adds Fire
  Damage"), the SPECIFIC necromancy omen for the target's side (Sinistral=prefix/Dextral=suffix)
  with reasoning, and the SPECIFIC lord omen (Sovereign/Liege/Blackblooded) with why it narrows
  the pool. New two-column UI: tailored steps on the left, a shopping list (items + approx
  quantities + ~total budget) on the right.
