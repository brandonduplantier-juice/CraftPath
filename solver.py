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


@dataclass(frozen=True)
class State:
    rarity: str
    secured: frozenset      # wanted mod_ids already on the item
    junk_pre: int
    junk_suf: int


class Solver:
    def __init__(self, mods, base_token, item_level, wanted_ids, prices,
                 essences=None, item_class=None, essence_prices=None,
                 desecrated=None, bone_cost=None, sinistral_omen_cost=None):
        self.base = base_token
        self.ilvl = item_level
        self.mods = {m.mod_id: m for m in mods}
        self.wanted = frozenset(wanted_ids)
        self.prices = prices                      # dict name -> exalted value

        # essence support: essences that force a WANTED mod onto this class
        self.item_class = item_class
        self.essence_prices = essence_prices or {}
        self.forcers = {}        # wanted_mod_id -> (essence_name, cost)
        if essences and item_class:
            for e in essences:
                fm = e.forced_mod(item_class)
                if fm in wanted_ids:
                    cost = self.essence_prices.get(e.name, 1e9)
                    # keep the cheapest essence that forces this mod
                    if fm not in self.forcers or cost < self.forcers[fm][1]:
                        self.forcers[fm] = (e.name, cost)

        # desecration support: a separate pool of desecrated mods (each with a
        # 'lord' and affix_type). A Bone + Sinistral/Dextral omen deterministically
        # adds an unrevealed slot of a chosen type; the reveal draws from this pool.
        # Reveal weights are unpublished, so we treat the pool as uniform and FLAG it.
        self.desecrated = desecrated or []          # list of mod-like dicts
        self.bone_cost = bone_cost if bone_cost is not None else 1e9
        self.sin_omen_cost = sinistral_omen_cost if sinistral_omen_cost is not None else 1e9
        # cost to abandon the current item and start fresh from a new white base.
        # White bases are cheap (vendor/drop); default 0.5 ex covers buying one.
        self.base_cost = self.prices.get("White Base", 0.5)
        self.desec_wanted_pre = [d for d in self.desecrated
                                 if d.get("affix_type") == "Prefix" and d["mod_id"] in wanted_ids]
        self.desec_wanted_suf = [d for d in self.desecrated
                                 if d.get("affix_type") == "Suffix" and d["mod_id"] in wanted_ids]

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
        open_pre = mp - sec_pre - s.junk_pre
        open_suf = ms - sec_suf - s.junk_suf
        return open_pre, open_suf, sec_pre, sec_suf

    def is_goal(self, s: State) -> bool:
        # secured if every wanted GROUP has an acceptable member present
        secured_groups = {self.mods[i].group for i in s.secured if i in self.mods}
        return self.wanted_groups <= secured_groups

    # ---- add-action outcome distribution --------------------------------
    def _add_outcomes(self, s: State, open_pre, open_suf):
        """Return list of (prob, next_state) for adding one random eligible mod.
        Landing ANY acceptable tier in a still-needed wanted group counts as a
        hit on that group (crafters want the stat, not one exact tier)."""
        secured_groups = {self.mods[i].group for i in s.secured if i in self.mods}
        needed_groups = self.wanted_groups - secured_groups
        # acceptable mod_ids that would secure a still-needed group
        acceptable = set()
        for g in needed_groups:
            acceptable |= self.wanted_group_members.get(g, set())
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
                elig[i] = w
        if open_suf > 0:
            for i, w in self.suf_w.items():
                g = self.mods[i].group
                if i in s.secured:
                    continue
                if g in self.wanted_groups and i not in acceptable:
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

    def _annul_outcomes(self, s: State, sec_pre, sec_suf):
        """Remove one random present mod (cannot target)."""
        present = len(s.secured) + s.junk_pre + s.junk_suf
        if present == 0:
            return []
        outs = []
        # remove a junk prefix / suffix
        if s.junk_pre > 0:
            outs.append((s.junk_pre / present,
                         State(s.rarity, s.secured, s.junk_pre - 1, s.junk_suf)))
        if s.junk_suf > 0:
            outs.append((s.junk_suf / present,
                         State(s.rarity, s.secured, s.junk_pre, s.junk_suf - 1)))
        # remove a secured wanted mod (bad) — each equally likely
        for i in s.secured:
            outs.append((1 / present,
                         State(s.rarity, s.secured - {i}, s.junk_pre, s.junk_suf)))
        return outs

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
            # Essence: deterministically force a wanted mod, Normal -> Magic.
            # Only useful for still-missing wanted mods this class can force.
            missing = self.wanted - s.secured
            for mid in missing:
                if mid in self.forcers:
                    ename, ecost = self.forcers[mid]
                    ns = State("Magic", s.secured | {mid}, s.junk_pre, s.junk_suf)
                    acts.append((f"Essence: {ename}", ecost, [(1.0, ns)]))
            outs = self._add_outcomes(State("Magic", s.secured, s.junk_pre, s.junk_suf), 1, 1)
            if outs:
                acts.append(("Transmutation Orb", self._cost("Transmutation Orb"), outs))
        elif s.rarity == "Magic":
            if (sec_pre + sec_suf + s.junk_pre + s.junk_suf) < 2:
                outs = self._add_outcomes(s, open_pre, open_suf)
                if outs:
                    acts.append(("Augmentation Orb", self._cost("Augmentation Orb"), outs))
            # Regal -> Rare, then adds one mod at Rare caps
            regal_state = State("Rare", s.secured, s.junk_pre, s.junk_suf)
            rp, rs, _, _ = self._slots(regal_state)
            routs = self._add_outcomes(regal_state, rp, rs)
            if routs:
                acts.append(("Regal Orb", self._cost("Regal Orb"), routs))
            aouts = self._annul_outcomes(s, sec_pre, sec_suf)
            if aouts:
                acts.append(("Orb of Annulment", self._cost("Orb of Annulment"), aouts))
        elif s.rarity == "Rare":
            if open_pre + open_suf > 0:
                outs = self._add_outcomes(s, open_pre, open_suf)
                if outs:
                    acts.append(("Exalted Orb", self._cost("Exalted Orb"), outs))
            aouts = self._annul_outcomes(s, sec_pre, sec_suf)
            if aouts:
                acts.append(("Orb of Annulment", self._cost("Orb of Annulment"), aouts))
            # Desecration: Bone + Sinistral omen forces an unrevealed PREFIX, then
            # reveal draws from the desecrated prefix pool. Only offered if a wanted
            # desecrated prefix exists and a prefix slot is open. Reveal weights are
            # unknown -> uniform over the desecrated prefix pool (flagged in output).
            if open_pre > 0 and self.desec_wanted_pre and self.desecrated:
                desec_pre_pool = [d for d in self.desecrated if d.get("affix_type") == "Prefix"]
                npool = len(desec_pre_pool)
                if npool:
                    cost = self.bone_cost + self.sin_omen_cost
                    outs = []
                    for d in self.desec_wanted_pre:
                        if d["mod_id"] not in s.secured:
                            ns = State(s.rarity, s.secured | {d["mod_id"]},
                                       s.junk_pre, s.junk_suf)
                            outs.append((1.0 / npool, ns))
                    # remaining probability lands on a non-wanted desecrated prefix (junk_pre)
                    p_hit = sum(p for p, _ in outs)
                    if p_hit < 1.0:
                        outs.append((1.0 - p_hit,
                                     State(s.rarity, s.secured, s.junk_pre + 1, s.junk_suf)))
                    if outs:
                        acts.append(("Desecrate prefix (Bone + Sinistral Omen)", cost, outs))
        return acts

    def _cost(self, name):
        v = self.prices.get(name)
        return v if v is not None else 1e9   # unknown price -> effectively avoid

    # ---- value iteration ------------------------------------------------
    def solve(self, start: State, max_iter=2000, tol=1e-6):
        # enumerate reachable states by BFS
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
        BIG = 1e12
        E = {s: (0.0 if self.is_goal(s) else BIG) for s in reach}
        policy = {}
        for _ in range(max_iter):
            changed = False
            for s in reach:
                if self.is_goal(s):
                    continue
                best, best_a = BIG, None
                for name, cost, outs in self.actions(s):
                    p_self = sum(p for p, ns in outs if ns == s)
                    other = sum(p * E[ns] for p, ns in outs if ns != s)
                    if p_self >= 1.0 - 1e-12:
                        continue
                    exp = (cost + other) / (1.0 - p_self)
                    if exp < best:
                        best, best_a = exp, name
                if best_a is not None and best < E[s] - tol:
                    E[s] = best
                    policy[s] = best_a
                    changed = True
            if not changed:
                break
        # anything still pinned near BIG cannot reach the goal under this model
        E = {s: (float("inf") if v >= BIG * 0.5 else v) for s, v in E.items()}
        return E, policy


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
