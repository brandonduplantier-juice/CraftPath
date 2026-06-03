# CraftPath — Backlog / TODO

Running list of things to add or fix, captured as they come up so nothing gets lost.
Newest items near the top of each section. Status: ☐ todo · ◐ in progress · ☑ done.

## High impact
- ☐ **Per-base weights pipeline.** Every base except dagger uses flat/uniform weights.
  The PoB source (`/home/claude/pob2/src/Data/ModItem.lua`) carries real per-tag
  weights — extract them per base the same way pools were generated. This is the #1
  thing limiting trustworthiness of non-dagger odds/costs. (Data job, not solver job.)

## Monetization (later)
- ☐ **Ads.** Replicate poe.ninja's unobtrusive ads. poe.ninja uses Google AdSense
  (per Similarweb) and Playwire (seen in their page source). AdSense is the easy
  first move but needs Google approval, which depends on traffic; revenue is small
  until traffic is large. Gaming networks (AdinPlay/Venatus) pay better but gate on
  volume. Plan: AdSense top+sidebar slots when ready → Venatus once traffic grows.
  Slots can be built behind a config flag and stay off until approved.

## Functionality
- ☐ **Conditional path / decision-tree view.** Present the plan as explicit branches:
  "if this → continue, if that → remove and roll again, if this → brick." The solver
  already computes failure-recovery per step; this is a presentation/UX layer on top.
- ☐ **Niche omens not yet modeled:** Coronation (Regal+side), Whittling, Erasure
  (Chaos+side), Alchemy omens. Each needs its own mechanic verification before adding.
  (Deliberately skipped: Homogenising Exaltation — removed 0.4.0; Corruption omens —
  removed 0.5.0; Recombination — removed this league.)

## Data refresh (run locally — needs network the dev box lacks)
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
