"""
solver.py
Expected-cost optimizing solver for PoE2 crafting.

This replaces the greedy step-picker with a proper Markov Decision Process.
A crafting state is:

    (rarity, secured_wanted_frozenset, junk_prefixes, junk_suffixes)

From any state, the available actions (Transmute / Augment / Regal / Exalt to
add a random eligible mod, Annul to remove a random mod) each have stochastic
outcomes whose probabilities come from the live mod weights. We compute, by
value iteration, the policy that MINIMIZES expected currency cost to reach the
target, and read the optimal action + expected cost off every state.

Because Annul-then-re-add forms cycles, this can't be a simple recursion; we
solve the fixed point E[s] = min_a ( cost(a) + Σ P(s'|s,a)·E[s'] ) iteratively.

Brick / budget risk: with Annul always available you can almost always recover
given enough currency, so true "brick" here = expected cost exceeding the
budget cap. The solver reports expected cost and flags when it exceeds budget.

MODELED SCOPE (stated honestly):
  - Core orbs only: Transmute, Augment, Regal, Exalt, Annul. Essences, Omens,
    Desecration, and Runes of Aldur are NOT yet in the optimization (they're
    declared in methods.py with confidence flags and will be layered in).
  - Junk mods are tracked by COUNT, not identity. This assumes junk does not
    fall into a wanted mod's group. When an add fills a wanted group with the
    wrong tier, that's the interactive planner's "blocker" case. For mostly
    weight-1 pools with few targets the approximation error is small.
  - Divine (perfecting numeric rolls) is treated as an optional final step, not
    part of reaching the mod SET, so it's reported separately.
"""
from __future__ import annotations
from dataclasses import dataclass
from itertools import combinations

# rarity -> (max_prefixes, max_suffixes)
CAPS = {"Normal": (0, 0), "Magic": (1, 1), "Rare": (3, 3)}

# Greater/Perfect currency tier floors (minimum modifier LEVEL that can roll).
# NOTE: these values are PATCH-VOLATILE and disputed across sources. The PoE2
# wiki lists Greater=35 / Perfect=50; a 0.5.0 (Runes of Aldur) guide reported
# Greater dropped to 44. Until confirmed in-game, defaulting to wiki values.
# Edit here if GGG/your testing confirms otherwise — flagged as ESTIMATE in UI.
TIER_FLOOR = {"greater": 44, "perfect": 50}  # Greater=44 confirmed (0.5 patch notes, was 55); Perfect=50 estimate


@dataclass(frozen=True)
class State:
    rarity: str
    secured: frozenset      # wanted mod_ids already on the item
    junk_pre: int
    junk_suf: int


class Solver:
    def __init__(self, mods, base_token, item_level, wanted_ids, prices,
                 essences=None, item_class=None, essence_prices=None,
                 desecrated=None, bone_cost=None, sinistral_omen_cost=None,
                 exalt_omen_cost=None, annul_omen_cost=None,
                 coronation_omen_cost=None, erasure_omen_cost=None,
                 enabled_methods=None):
        self.base = base_token
        self.ilvl = item_level
        self.mods = {m.mod_id: m for m in mods}
        self.wanted = frozenset(wanted_ids)
        self.prices = prices                      # dict name -> exalted value
        # enabled_methods: None/empty -> ALL methods on. Otherwise a set of
        # optional-method keys ('essence','tiered','omens') that are allowed;
        # basic orbs (transmute/aug/regal/exalt/annul/alchemy/chaos/restart) are
        # ALWAYS allowed so the item is never unsolvable.
        self._methods = set(enabled_methods) if enabled_methods else None

        # essence support: essences that force a WANTED mod onto this class
        self.item_class = item_class
        self.essence_prices = essence_prices or {}
        self.forcers = {}        # wanted_mod_id -> (essence_name, cost)
        self._essences = essences          # defer forcer mapping until groups built
        self._item_class = item_class

        # desecration support: a separate pool of desecrated mods (each with a
        # 'lord' and affix_type). A Bone + Sinistral/Dextral omen deterministically
        # adds an unrevealed slot of a chosen type; the reveal draws from this pool.
        # Reveal weights are unpublished, so we treat the pool as uniform and FLAG it.
        self.desecrated = desecrated or []          # list of mod-like dicts
        self.bone_cost = bone_cost if bone_cost is not None else 1e9
        self.sin_omen_cost = sinistral_omen_cost if sinistral_omen_cost is not None else 1e9
        # cost of a Sinistral/Dextral Exaltation omen (steers next Exalt to one
        # side). None -> feature off (omen not priced/available).
        self.exalt_omen_cost = exalt_omen_cost
        self.annul_omen_cost = annul_omen_cost
        self.coronation_omen_cost = coronation_omen_cost
        self.erasure_omen_cost = erasure_omen_cost
        # cost to abandon the current item and start fresh from a new white base.
        # White bases are cheap (vendor/drop); default 0.5 ex covers buying one.
        self.base_cost = self.prices.get("White Base", 0.5)
        self.desec_wanted_pre = [d for d in self.desecrated
                                 if d.get("affix_type") == "Prefix" and d["mod_id"] in wanted_ids]
        self.desec_wanted_suf = [d for d in self.desecrated
                                 if d.get("affix_type") == "Suffix" and d["mod_id"] in wanted_ids]
        # ids of wanted desecrated mods (not in the regular pool) — these must also
        # be satisfied for the goal, and occupy slots once secured.
        self.desec_wanted_pre_ids = {d["mod_id"] for d in self.desec_wanted_pre}
        self.desec_wanted_suf_ids = {d["mod_id"] for d in self.desec_wanted_suf}

        # partition wanted by slot type
        self.wanted_pre = {i for i in wanted_ids
                           if i in self.mods and self.mods[i].affix_type == "Prefix"}
        self.wanted_suf = {i for i in wanted_ids
                           if i in self.mods and self.mods[i].affix_type == "Suffix"}
        self.wanted_groups = {self.mods[i].group for i in wanted_ids if i in self.mods}
        # map each wanted group -> the set of eligible mod_ids in that group at or
        # above the requested tier's value (any of them counts as securing the
        # group). Crafters mean "I want this stat", not one exact tier.
        self.wanted_group_members = {}
        for wid in wanted_ids:
            if wid not in self.mods:
                continue
            g = self.mods[wid].group
            want_level = self.mods[wid].level
            members = {i for i, m in self.mods.items()
                       if m.group == g and m.level <= self.ilvl and m.level >= want_level}
            # always include the explicitly requested mod
            members.add(wid)
            self.wanted_group_members[g] = members

        # Essence forcers: an essence helps if its forced mod is an ACCEPTABLE
        # member of a wanted group (same "want the stat, not the exact tier"
        # logic as slamming) — not only an exact mod_id match. We record it
        # against the wanted mod_id whose group it satisfies.
        if self._essences and self._item_class:
            # Essence matching: an essence forces a FIXED tier. It satisfies a
            # wanted target only if that forced tier is AT OR ABOVE the wanted
            # tier (same rule as slamming — a lower tier is a genuine miss, so we
            # don't pretend a tier-2 Lesser essence satisfies a tier-10 target).
            # Choosing the essence is then a guaranteed win for that stat.
            wid_by_group = {}
            for wid in wanted_ids:
                if wid in self.mods:
                    wid_by_group.setdefault(self.mods[wid].group, []).append(wid)
            for e in self._essences:
                fm = e.forced_mod(self._item_class)
                if not fm or fm not in self.mods:
                    continue
                fmod = self.mods[fm]
                # find a wanted target in the same group whose tier this essence
                # meets or beats
                target_wid = None
                for wid in wid_by_group.get(fmod.group, []):
                    if fmod.level >= self.mods[wid].level:
                        target_wid = wid
                        break
                if target_wid is None:
                    continue
                nm = e.name.lower()
                if nm.startswith("greater"):
                    tier = "greater"
                elif nm.startswith("perfect"):
                    tier = "perfect"
                else:
                    tier = "normal"   # Lesser + Normal both go Normal->Magic
                cost = self.essence_prices.get(e.name, 1e9)
                key = (target_wid, tier)
                if key not in self.forcers or cost < self.forcers[key][1]:
                    self.forcers[key] = (e.name, cost, fm)
        self._action_cache = {}
        self._prep_pool()

    def _prep_pool(self):
        self.pre_w, self.suf_w = {}, {}     # mod_id -> weight, for addable mods
        for m in self.mods.values():
            if m.level > self.ilvl:
                continue
            w = m.weight_for(self.base)
            if w <= 0:
                continue
            if m.affix_type == "Prefix":
                self.pre_w[m.mod_id] = w
            else:
                self.suf_w[m.mod_id] = w

    # ---- slot accounting ------------------------------------------------
    def _slots(self, s: State):
        mp, ms = CAPS[s.rarity]
        sec_pre = sum(i in self.wanted_pre for i in s.secured)
        sec_suf = sum(i in self.wanted_suf for i in s.secured)
        # secured wanted-desecrated mods occupy slots too
        sec_pre += sum(i in self.desec_wanted_pre_ids for i in s.secured)
        sec_suf += sum(i in self.desec_wanted_suf_ids for i in s.secured)
        open_pre = mp - sec_pre - s.junk_pre
        open_suf = ms - sec_suf - s.junk_suf
        return open_pre, open_suf, sec_pre, sec_suf

    def is_goal(self, s: State) -> bool:
        # secured if every wanted GROUP has an acceptable member present...
        secured_groups = {self.mods[i].group for i in s.secured if i in self.mods}
        if not (self.wanted_groups <= secured_groups):
            return False
        # ...AND every wanted desecrated mod (not in the regular pool) is secured
        need_desec = self.desec_wanted_pre_ids | self.desec_wanted_suf_ids
        return need_desec <= s.secured

    # ---- add-action outcome distribution --------------------------------
    def _add_outcomes(self, s: State, open_pre, open_suf, min_level=0):
        """Return list of (prob, next_state) for adding one random eligible mod.
        Landing ANY acceptable tier in a still-needed wanted group counts as a
        hit on that group (crafters want the stat, not one exact tier).

        min_level models Greater/Perfect currency: it raises the minimum mod
        level that can roll (Greater=35, Perfect=50). Per GGG's rule, if ALL
        tiers of a mod would be excluded by the floor, that mod's HIGHEST tier
        remains eligible (the floor never excludes a mod type entirely)."""
        secured_groups = {self.mods[i].group for i in s.secured if i in self.mods}
        needed_groups = self.wanted_groups - secured_groups
        # acceptable mod_ids that would secure a still-needed group
        acceptable = set()
        for g in needed_groups:
            acceptable |= self.wanted_group_members.get(g, set())
        # precompute, per group, the highest-level tier (always stays eligible
        # under a min_level floor so the mod type is never fully excluded)
        top_tier_of_group = {}
        if min_level > 0:
            for i in list(self.pre_w) + list(self.suf_w):
                g = self.mods[i].group
                cur = top_tier_of_group.get(g)
                if cur is None or self.mods[i].level > self.mods[cur].level:
                    top_tier_of_group[g] = i
        def _passes_floor(i):
            if min_level <= 0 or self.mods[i].level >= min_level:
                return True
            # below floor: only allowed if it's the top tier of its group
            return top_tier_of_group.get(self.mods[i].group) == i
        # eligible weights given which slot types are open and groups free
        elig = {}
        if open_pre > 0:
            for i, w in self.pre_w.items():
                g = self.mods[i].group
                if i in s.secured:
                    continue
                # a mod in a wanted group that is NOT an acceptable hit -> junk
                if g in self.wanted_groups and i not in acceptable:
                    continue
                if not _passes_floor(i):
                    continue
                elig[i] = w
        if open_suf > 0:
            for i, w in self.suf_w.items():
                g = self.mods[i].group
                if i in s.secured:
                    continue
                if g in self.wanted_groups and i not in acceptable:
                    continue
                if not _passes_floor(i):
                    continue
                elig[i] = w
        W = sum(elig.values())
        if W == 0:
            return []
        outs = []
        junk_pre_w = sum(w for i, w in elig.items()
                         if self.mods[i].affix_type == "Prefix" and i not in acceptable)
        junk_suf_w = sum(w for i, w in elig.items()
                         if self.mods[i].affix_type == "Suffix" and i not in acceptable)
        # group acceptable landings by which group they secure (collapse tiers)
        group_hit_w = {}
        for i, w in elig.items():
            if i in acceptable:
                g = self.mods[i].group
                group_hit_w.setdefault(g, []).append((i, w))
        for g, members in group_hit_w.items():
            gw = sum(w for _, w in members)
            # represent the hit by the highest-value member landed (best tier)
            best = max(members, key=lambda x: self.mods[x[0]].level)[0]
            outs.append((gw / W,
                         State(s.rarity, s.secured | {best}, s.junk_pre, s.junk_suf)))
        if junk_pre_w > 0:
            outs.append((junk_pre_w / W,
                         State(s.rarity, s.secured, s.junk_pre + 1, s.junk_suf)))
        if junk_suf_w > 0:
            outs.append((junk_suf_w / W,
                         State(s.rarity, s.secured, s.junk_pre, s.junk_suf + 1)))
        return outs

    def _annul_outcomes(self, s: State, sec_pre, sec_suf, side=None):
        """Remove one random present mod (cannot target which one).
        side='Prefix'/'Suffix' (annul omen) restricts removal to that side."""
        # which mods are removable given the side restriction
        def is_pre(i):
            return (i in self.mods and self.mods[i].affix_type == "Prefix") \
                   or i in self.desec_wanted_pre_ids
        sec_list = list(s.secured)
        if side == "Prefix":
            jpre, jsuf = s.junk_pre, 0
            secs = [i for i in sec_list if is_pre(i)]
        elif side == "Suffix":
            jpre, jsuf = 0, s.junk_suf
            secs = [i for i in sec_list if not is_pre(i)]
        else:
            jpre, jsuf = s.junk_pre, s.junk_suf
            secs = sec_list
        present = len(secs) + jpre + jsuf
        if present == 0:
            return []
        outs = []
        if jpre > 0:
            outs.append((jpre / present,
                         State(s.rarity, s.secured, s.junk_pre - 1, s.junk_suf)))
        if jsuf > 0:
            outs.append((jsuf / present,
                         State(s.rarity, s.secured, s.junk_pre, s.junk_suf - 1)))
        for i in secs:
            outs.append((1 / present,
                         State(s.rarity, s.secured - {i}, s.junk_pre, s.junk_suf)))
        return outs

    def _alchemy_outcomes(self, s: State):
        """Orb of Alchemy: Normal -> Rare with multiple random mods at once
        (GGG: 4-6). Modeled as 4 sequential random adds from the Rare state,
        composing the per-add distribution. Conservative (4, the minimum)."""
        cur = [(1.0, State("Rare", s.secured, s.junk_pre, s.junk_suf))]
        for _ in range(4):
            nxt = {}
            for p, st in cur:
                op, osf, _, _ = self._slots(st)
                if op + osf <= 0:
                    nxt[st] = nxt.get(st, 0) + p
                    continue
                adds = self._add_outcomes(st, op, osf)
                if not adds:
                    nxt[st] = nxt.get(st, 0) + p
                    continue
                for q, ns in adds:
                    nxt[ns] = nxt.get(ns, 0) + p * q
            cur = list(nxt.items())
            cur = [(p, st) for st, p in nxt.items()]
        return cur

    def _chaos_outcomes(self, s: State, sec_pre, sec_suf, open_pre, open_suf, side=None):
        """Chaos Orb (PoE2 0.5): removes ONE random mod, then adds ONE new
        random mod. Composed as annul-distribution followed by add-distribution.
        With side='Prefix'/'Suffix' (Sinistral/Dextral Erasure omen), the REMOVAL
        is restricted to that side (then the add is unrestricted, as Chaos adds
        a random new mod to any open slot)."""
        removed = self._annul_outcomes(s, sec_pre, sec_suf, side=side)
        if not removed:
            return []
        out = {}
        for p, st in removed:
            op, osf, _, _ = self._slots(st)
            adds = self._add_outcomes(st, op, osf)
            if not adds:
                out[st] = out.get(st, 0) + p
                continue
            for q, ns in adds:
                out[ns] = out.get(ns, 0) + p * q
        return [(p, st) for st, p in out.items()]


    # ---- enumerate applicable (action, cost, outcomes) ------------------
    def actions(self, s: State):
        # Cache: a state's action set never changes, but value iteration asks
        # for it thousands of times. Compute once, reuse. (Pure memoization —
        # identical results, far less work; critical on slow/low-CPU hosts.)
        cached = self._action_cache.get(s)
        if cached is not None:
            return cached
        result = self._compute_actions(s)
        self._action_cache[s] = result
        return result

    def _compute_actions(self, s: State):
        open_pre, open_suf, sec_pre, sec_suf = self._slots(s)
        acts = []
        # Restart: abandon this item, buy a fresh white base, start from Normal.
        # Available from any non-pristine state. This is the realistic cheap path
        # for single-mod crafts (transmute, and if you miss, just try a new base)
        # — without it the solver overpays by annul-cycling a ruined item.
        # NOTE: we offer restart even when wanted mods are already secured. The
        # value iteration only picks it if it's genuinely cheaper than finishing
        # the current item — which honestly reflects that a partly-done item is
        # sometimes worse than a fresh base (you'd scrap it). It never forces a
        # restart that loses progress unless that's truly the cheaper expectation.
        if s.rarity != "Normal" or s.junk_pre or s.junk_suf:
            fresh = State("Normal", frozenset(), 0, 0)
            if fresh != s:
                acts.append(("Restart (fresh base)", self.base_cost, [(1.0, fresh)]))
        if s.rarity == "Normal":
            # Lesser/Normal Essence: Normal -> Magic (+ guaranteed mod).
            if self._on("essence"):
                missing = self.wanted - s.secured
                for mid in missing:
                    key = (mid, "normal")
                    if key in self.forcers:
                        ename, ecost, _fm = self.forcers[key]
                        ns = State("Magic", s.secured | {mid}, s.junk_pre, s.junk_suf)
                        acts.append((f"Essence: {ename}", ecost, [(1.0, ns)]))
            # Transmutation (always) + Greater/Perfect variants (gated by 'tiered').
            tmuts = [("Transmutation Orb", 0)]
            if self._on("tiered"):
                tmuts += [("Greater Orb of Transmutation", TIER_FLOOR["greater"]),
                          ("Perfect Orb of Transmutation", TIER_FLOOR["perfect"])]
            for label, floor in tmuts:
                c = self._cost(label)
                if c >= 1e9:
                    continue
                outs = self._add_outcomes(State("Magic", s.secured, s.junk_pre, s.junk_suf),
                                          1, 1, min_level=floor)
                if outs:
                    acts.append((label, c, outs))
            # Orb of Alchemy: Normal -> Rare with several random mods at once.
            # GGG: 4-6 mods; modeled as 4 random adds (conservative) in one step.
            ac = self._cost("Orb of Alchemy")
            if ac < 1e9:
                alch = self._alchemy_outcomes(s)
                if alch:
                    acts.append(("Orb of Alchemy", ac, alch))
        elif s.rarity == "Magic":
            # Greater Essence: Magic -> Rare (+ guaranteed mod), keeping existing
            # magic mods. This is the core "buy magic base, essence it" flow.
            if self._on("essence"):
                missing = self.wanted - s.secured
                for mid in missing:
                    key = (mid, "greater")
                    if key in self.forcers:
                        ename, ecost, _fm = self.forcers[key]
                        ns = State("Rare", s.secured | {mid}, s.junk_pre, s.junk_suf)
                        acts.append((f"Essence: {ename}", ecost, [(1.0, ns)]))
            if (sec_pre + sec_suf + s.junk_pre + s.junk_suf) < 2:
                augs = [("Augmentation Orb", 0)]
                if self._on("tiered"):
                    augs += [("Greater Orb of Augmentation", TIER_FLOOR["greater"]),
                             ("Perfect Orb of Augmentation", TIER_FLOOR["perfect"])]
                for label, floor in augs:
                    c = self._cost(label)
                    if c >= 1e9:
                        continue
                    outs = self._add_outcomes(s, open_pre, open_suf, min_level=floor)
                    if outs:
                        acts.append((label, c, outs))
            # Regal -> Rare (+ Greater/Perfect tier floors gated by 'tiered')
            regal_state = State("Rare", s.secured, s.junk_pre, s.junk_suf)
            rp, rs, _, _ = self._slots(regal_state)
            regals = [("Regal Orb", 0)]
            if self._on("tiered"):
                regals += [("Greater Regal Orb", TIER_FLOOR["greater"]),
                           ("Perfect Regal Orb", TIER_FLOOR["perfect"])]
            for label, floor in regals:
                c = self._cost(label)
                if c >= 1e9:
                    continue
                routs = self._add_outcomes(regal_state, rp, rs, min_level=floor)
                if routs:
                    acts.append((label, c, routs))
            # Sinistral/Dextral Coronation: next Regal adds ONLY a prefix / ONLY a
            # suffix. Gated by 'omens'. Steers the Magic->Rare upgrade to the side
            # that still has a wanted mod instead of gambling which side gets it.
            if self._on("omens") and self.coronation_omen_cost is not None:
                ccost = self._cost("Regal Orb") + self.coronation_omen_cost
                if rp > 0:
                    so = self._add_outcomes(regal_state, rp, 0)   # prefix only
                    if so:
                        acts.append(("Regal Orb + Omen of Sinistral Coronation", ccost, so))
                if rs > 0:
                    do = self._add_outcomes(regal_state, 0, rs)   # suffix only
                    if do:
                        acts.append(("Regal Orb + Omen of Dextral Coronation", ccost, do))
            aouts = self._annul_outcomes(s, sec_pre, sec_suf)
            if aouts:
                acts.append(("Orb of Annulment", self._cost("Orb of Annulment"), aouts))
        elif s.rarity == "Rare":
            # Perfect Essence: on a Rare, REMOVES a random mod then ADDS a
            # guaranteed mod. Models the highest-tier targeted craft. Composed as
            # annul-distribution (random removal) then deterministic add of the
            # forced mod. Only offered for still-missing wanted mods.
            if self._on("essence"):
                missing = self.wanted - s.secured
                for mid in missing:
                    key = (mid, "perfect")
                    if key in self.forcers:
                        ename, ecost, _fm = self.forcers[key]
                        rem = self._annul_outcomes(s, sec_pre, sec_suf)
                        if rem:
                            outs = []
                            for p, ns in rem:
                                outs.append((p, State(ns.rarity, ns.secured | {mid},
                                                      ns.junk_pre, ns.junk_suf)))
                        else:
                            outs = [(1.0, State(s.rarity, s.secured | {mid},
                                                s.junk_pre, s.junk_suf))]
                        acts.append((f"Essence: {ename}", ecost, outs))
            # Exalted Orb (always) + Greater/Perfect variants (gated by 'tiered')
            if open_pre + open_suf > 0:
                exalts = [("Exalted Orb", 0)]
                if self._on("tiered"):
                    exalts += [("Greater Exalted Orb", TIER_FLOOR["greater"]),
                               ("Perfect Exalted Orb", TIER_FLOOR["perfect"])]
                for label, floor in exalts:
                    c = self._cost(label)
                    if c >= 1e9:
                        continue
                    outs = self._add_outcomes(s, open_pre, open_suf, min_level=floor)
                    if outs:
                        acts.append((label, c, outs))
            # Sinistral/Dextral Exaltation: an omen steers the next Exalted Orb to
            # add ONLY a prefix (Sinistral) or ONLY a suffix (Dextral). Gated by
            # 'omens'. Lets the plan use one steered exalt instead of gambling two.
            if self._on("omens") and self.exalt_omen_cost is not None:
                ecost = self._cost("Exalted Orb") + self.exalt_omen_cost
                if open_pre > 0:
                    souts = self._add_outcomes(s, open_pre, 0)   # prefix only
                    if souts:
                        acts.append(("Exalted Orb + Omen of Sinistral Exaltation",
                                     ecost, souts))
                if open_suf > 0:
                    douts = self._add_outcomes(s, 0, open_suf)   # suffix only
                    if douts:
                        acts.append(("Exalted Orb + Omen of Dextral Exaltation",
                                     ecost, douts))
            aouts = self._annul_outcomes(s, sec_pre, sec_suf)
            if aouts:
                acts.append(("Orb of Annulment", self._cost("Orb of Annulment"), aouts))
            # Sinistral/Dextral Annulment omens: remove ONLY a prefix / ONLY a
            # suffix. Gated by 'omens'. Surgically drop a junk mod from one side.
            if self._on("omens") and self.annul_omen_cost is not None:
                acost = self._cost("Orb of Annulment") + self.annul_omen_cost
                ap = self._annul_outcomes(s, sec_pre, sec_suf, side="Prefix")
                if ap:
                    acts.append(("Annul + Omen of Sinistral Annulment", acost, ap))
                as_ = self._annul_outcomes(s, sec_pre, sec_suf, side="Suffix")
                if as_:
                    acts.append(("Annul + Omen of Dextral Annulment", acost, as_))
            # Chaos Orb (0.5 behaviour): removes ONE random mod and adds ONE new
            # random mod (NOT a full reroll). Modeled as annul-then-add combined.
            cc = self._cost("Chaos Orb")
            if cc < 1e9:
                ch = self._chaos_outcomes(s, sec_pre, sec_suf, open_pre, open_suf)
                if ch:
                    acts.append(("Chaos Orb", cc, ch))
                # Sinistral/Dextral Erasure: next Chaos removes ONLY a prefix /
                # ONLY a suffix (then adds a random new mod). Gated by 'omens'.
                # Lets you reroll a junk mod on a known side without risking the
                # keeper on the other side.
                if self._on("omens") and self.erasure_omen_cost is not None:
                    ecost = cc + self.erasure_omen_cost
                    chp = self._chaos_outcomes(s, sec_pre, sec_suf, open_pre, open_suf, side="Prefix")
                    if chp:
                        acts.append(("Chaos Orb + Omen of Sinistral Erasure", ecost, chp))
                    chs = self._chaos_outcomes(s, sec_pre, sec_suf, open_pre, open_suf, side="Suffix")
                    if chs:
                        acts.append(("Chaos Orb + Omen of Dextral Erasure", ecost, chs))
            # NOTE: desecration (Bone+Omen) is intentionally NOT a solver action.
            # Its reveal odds are unpublished, and as a low-probability self-
            # looping action it destabilized the exact linear solve (produced
            # spurious/negative costs). Desecrated targets are handled by the
            # dedicated Putrefaction recommendation panel instead, which models
            # the real method with clearly-flagged estimates.
        return acts

    def _cost(self, name):
        v = self.prices.get(name)
        return v if v is not None else 1e9   # unknown price -> effectively avoid

    def _on(self, category):
        """Is an OPTIONAL method category allowed? None -> all on."""
        return self._methods is None or category in self._methods

    # ---- value iteration ------------------------------------------------
    def solve(self, start: State, max_iter=200, tol=1e-9, time_budget=20.0):
        """Exact solve via POLICY ITERATION.

        The MDP has self-referential cycles (restart returns to a fresh base;
        annul can remove a secured mod), which make naive value iteration mix
        very slowly. Policy iteration instead (a) evaluates the current policy
        EXACTLY by solving the linear system V = c + P V, then (b) improves the
        policy greedily, and repeats. It converges in a handful of iterations
        and the answer is exact and deterministic (no sweep-order / hash-seed
        dependence, no convergence-budget guesswork)."""
        import numpy as np
        import time as _time
        _t0 = _time.time()

        # 1) enumerate reachable states (deterministic order)
        reach, frontier = set(), [start]
        while frontier:
            s = frontier.pop()
            if s in reach:
                continue
            reach.add(s)
            if self.is_goal(s):
                continue
            for _, _, outs in self.actions(s):
                for _, ns in outs:
                    if ns not in reach:
                        frontier.append(ns)

        def _skey(st):
            return (-len(st.secured), st.rarity, st.junk_pre, st.junk_suf,
                    tuple(sorted(st.secured)))
        states = sorted(reach, key=_skey)
        idx = {s: i for i, s in enumerate(states)}
        n = len(states)
        goal = np.array([self.is_goal(s) for s in states])

        # precompute each state's actions once (cached in self._action_cache too)
        acts_of = {s: self.actions(s) for s in states if not self.is_goal(s)}

        # --- reachability: which states can reach a goal at all? A goal that no
        # modeled action can produce (e.g. a desecrated-only mod, unreachable by
        # orb-slamming) must come back as INFINITE cost so the caller routes to
        # putrefaction, NOT a spurious finite value from the linear solve. ---
        can_reach = set(s for s in states if self.is_goal(s))
        changed = True
        while changed:
            changed = False
            for s in states:
                if s in can_reach or self.is_goal(s):
                    continue
                for _, _, outs in acts_of[s]:
                    if any(ns in can_reach for p, ns in outs):
                        can_reach.add(s); changed = True; break
        if start not in can_reach:
            # goal genuinely unreachable under modeled methods -> bricked/infinite
            E = {s: (0.0 if self.is_goal(s) else float("inf")) for s in states}
            self.converged = True
            return E, {}

        # only states that can reach a goal participate in the solve; the rest
        # are infinite-cost dead ends (their actions are pruned below).
        unreachable = set(states) - can_reach
        states = [s for s in states if s in can_reach]
        idx = {s: i for i, s in enumerate(states)}
        n = len(states)
        goal = np.array([self.is_goal(s) for s in states])

        INF = float("inf")
        # 2) initial proper policy: cheapest single action that has SOME chance
        #    of leaving the state (avoids degenerate all-self-loop start).
        policy = {}
        for s in states:
            if self.is_goal(s):
                continue
            best = None
            for name, cost, outs in acts_of[s]:
                # an action that can land in an unreachable dead end is unusable
                if any(ns in unreachable for p, ns in outs):
                    continue
                p_self = sum(p for p, ns in outs if ns == s)
                if p_self < 1.0 - 1e-12:
                    if best is None or cost < best[1]:
                        best = (name, cost, outs)
            policy[s] = best  # may be None if truly stuck (handled below)

        self.converged = False
        V = np.zeros(n)

        for _pi in range(max_iter):
            # --- policy evaluation: solve V = c + P V exactly ---
            A = np.zeros((n, n))
            b = np.zeros(n)
            for i, s in enumerate(states):
                if goal[i]:
                    A[i, i] = 1.0
                    b[i] = 0.0
                    continue
                act = policy.get(s)
                if act is None:
                    A[i, i] = 1.0
                    b[i] = 1e12      # stuck -> unreachable cost
                    continue
                name, cost, outs = act
                A[i, i] = 1.0
                b[i] = cost
                for p, ns in outs:
                    A[i, idx[ns]] -= p
            try:
                V = np.linalg.solve(A, b)
            except np.linalg.LinAlgError:
                V = np.linalg.lstsq(A, b, rcond=None)[0]

            # --- policy improvement: pick the best action given V ---
            stable = True
            for i, s in enumerate(states):
                if goal[i]:
                    continue
                best_v, best_a = INF, None
                for name, cost, outs in acts_of[s]:
                    if any(ns in unreachable for p, ns in outs):
                        continue
                    p_self = sum(p for p, ns in outs if ns == s)
                    if p_self >= 1.0 - 1e-12:
                        continue
                    other = sum(p * V[idx[ns]] for p, ns in outs if ns != s)
                    q = (cost + other) / (1.0 - p_self)
                    if q < best_v - 1e-12:
                        best_v, best_a = q, (name, cost, outs)
                if best_a is not None:
                    prev = policy.get(s)
                    if prev is None or prev[0] != best_a[0]:
                        stable = False
                    policy[s] = best_a
            if stable:
                self.converged = True
                break
            if _time.time() - _t0 > time_budget:
                break

        # build outputs in the legacy dict form the caller expects
        BIG = 1e12
        E = {}
        for i, s in enumerate(states):
            v = V[i]
            E[s] = float("inf") if v >= BIG * 0.5 else float(v)
        for s in unreachable:
            E[s] = float("inf")
        pol = {s: act[0] for s, act in policy.items() if act is not None}
        return E, pol


# --------------------------------------------------------------------------
def _load_prices():
    try:
        from prices import load_prices
        meta = load_prices()
        if meta and meta.get("prices"):
            return meta["prices"], meta.get("league", "?")
    except Exception:
        pass
    # fallback to the live values Brandon observed (Runes of Aldur, 2026-06-02)
    return ({"Transmutation Orb": 0.1273, "Augmentation Orb": 0.0849,
             "Regal Orb": 0.4438, "Exalted Orb": 1.0, "Chaos Orb": 1.0243,
             "Orb of Annulment": 11.6712, "Divine Orb": 50.9966},
            "Runes of Aldur (observed fallback)")


def demo():
    import json
    raw = json.load(open("data/claw_mods.json"))["mods"]
    class M:
        def __init__(s, d): s.__dict__.update(d)
        def weight_for(s, _): return s.weight
    mods = [M(d) for d in raw]
    by_id = {m.mod_id: m for m in mods}
    prices, league = _load_prices()

    wanted = ["LocalAddedFireDamage9", "Dexterity8"]   # 1 prefix + 1 suffix
    solver = Solver(mods, "claw", 81, wanted, prices)
    start = State("Normal", frozenset(), 0, 0)
    E, policy = solver.solve(start)

    print(f"Prices: {league}")
    print("TARGET:")
    for i in wanted:
        print(f"  {i:<24} ({by_id[i].affix_type}) {by_id[i].text[0]}")
    print(f"\nExpected total cost (optimal policy): {E[start]:.2f} ex")
    print("\nOptimal next action by state (walk from start):")
    s = start
    seen = set()
    step = 0
    while not solver.is_goal(s) and s not in seen and step < 12:
        seen.add(s); step += 1
        a = policy.get(s)
        if a is None:
            print("  (no action — dead end)"); break
        print(f"  step {step}: {s.rarity:<6} secured={sorted(set(s.secured))} "
              f"junk=({s.junk_pre}p,{s.junk_suf}s)  ->  {a}  "
              f"[E={E[s]:.2f} ex]")
        # advance along the MOST LIKELY outcome to illustrate the path
        outs = next(o for n, c, o in solver.actions(s) if n == a)
        s = max(outs, key=lambda po: po[0])[1]
    print(f"\nReachable states explored: {len(E)}")


if __name__ == "__main__":
    demo()
