# CraftPath — Backlog / TODO

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
  (the original scrape was missing 1271 mods total — talisman 46->239 valid, wand
  105->276, sceptre 150->257, staff 196->276). Also surfaced 4 triple-attribute
  armour bases (body/boots/gloves/helmet _str_dex_int) that had 0 mods / didn't
  exist — now added to bases_index.json (56 bases total). dagger keeps CoE overlay.

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
  directly selectable as targets — clicking one adds a 🦴-marked chip; any desecrated
  target routes the plan to the Putrefaction (Well of Souls) how-to guide.
- ☐ **Conditional path / decision-tree view.** Present the plan as explicit branches:
  "if this → continue, if that → remove and roll again, if this → brick." The solver
  already computes failure-recovery per step; this is a presentation/UX layer on top.
- ☐ **Niche omens not yet modeled:** Coronation (Regal+side), Whittling, Erasure
  (Chaos+side), Alchemy omens. Each needs its own mechanic verification before adding.
  (Deliberately skipped: Homogenising Exaltation — removed 0.4.0; Corruption omens —
  removed 0.5.0; Recombination — removed this league.)

## Data refresh (run locally — needs network the dev box lacks)
- ☑ **Desecrated base-type filtering.** DONE via build_desecrated.py: rebuilt
  data/desecrated_mods.json from PoB ModVeiled.lua (196 lord mods: ulaman/amanamu/
  kurgal) WITH real per-base weightKey tags. /api/desecrated and the solve pool now
  filter to mods that can actually roll on the base (e.g. quarterstaff shows 6pre/6suf
  weapon mods, not the old flat 70/125 with Charms/Spells/Curses). Tecrod/Kulemak NOT
  added — they're Timeless-Jewel lords, not Well-of-Souls reveal mods (verified).
- ☐ **Essence prices** seeded as TIERED ESTIMATES (Lesser~0.5 / Normal~2 / Greater~8
  / Perfect~25 ex). Run `python prices.py` locally to overwrite with live PoE2 Scout
  values. A few high-demand Greater essences really trade 50+ ex.
- ☐ **Currency/omen prices** are a manual snapshot; refresh with `python prices.py`.
  Tier-floor values (Greater~35 / Perfect~50) are patch-disputed estimates in solver.py.

## Item art (deferred by Brandon)
- ☐ **Real item art URLs.** Source = `web.poecdn.com` (GGG's own CDN — what poe.ninja
  /poe2db reference). Per-base icon path is encoded/unguessable, so it needs a one-time
  LOCAL harvester to populate `BASE_ART` in forge.html from poecdn URLs. Placeholder
  silhouettes + GGG credit are live now. Deferred until other functionality is solid.

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
