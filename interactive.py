"""
interactive.py
Drives the optimizing solver as a step-by-step crafting assistant.

Flow (the original spec):
  1. You describe the item: base class, item level, target mods, budget.
  2. The solver computes the optimal policy (cheapest expected cost, which also
     minimizes risky Annul use).
  3. At each step it tells you the recommended currency, the odds of the useful
     outcomes, the expected remaining cost, and whether you're within budget.
  4. You tell it what actually rolled; it re-locates your true state in the
     policy and gives the next step — or says DONE or BUDGET-EXCEEDED.

This module is UI-agnostic: `next_step()` and `apply_outcome()` are pure
functions over solver state, so the same engine backs the CLI here and a future
GUI. No network and no game knowledge is hardcoded beyond the solver.
"""
from __future__ import annotations
import json
from solver import Solver, State, _load_prices
from essences import parse_essences


class Session:
    def __init__(self, mods, base_token, item_level, wanted_ids, prices,
                 budget=None, essences=None, item_class=None, essence_prices=None):
        self.solver = Solver(mods, base_token, item_level, wanted_ids, prices,
                             essences=essences, item_class=item_class,
                             essence_prices=essence_prices)
        self.by_id = {m.mod_id: m for m in mods}
        self.budget = budget
        self.E, self.policy = self.solver.solve(State("Normal", frozenset(), 0, 0))
        self.state = State("Normal", frozenset(), 0, 0)
        self.spent = 0.0

    # ---- core API (UI-agnostic) ----------------------------------------
    def next_step(self) -> dict:
        sv, s = self.solver, self.state
        if sv.is_goal(s):
            return {"status": "done",
                    "msg": "Target met. Optionally Divine to perfect numeric rolls.",
                    "spent": round(self.spent, 2)}
        exp_remaining = self.E.get(s, float("inf"))
        if exp_remaining == float("inf"):
            return {"status": "bricked",
                    "msg": "No path to target from the current state under the "
                           "modeled methods. Item is bricked for this goal.",
                    "spent": round(self.spent, 2)}
        if self.budget is not None and self.spent + exp_remaining > self.budget:
            return {"status": "over_budget",
                    "msg": (f"Expected remaining cost {exp_remaining:.1f} ex pushes "
                            f"total past your {self.budget} ex budget. Consider "
                            f"buying the finished item instead."),
                    "spent": round(self.spent, 2),
                    "expected_remaining": round(exp_remaining, 1)}

        action = self.policy.get(s)
        outs = next(o for n, c, o in sv.actions(s) if n == action)
        cost = next(c for n, c, o in sv.actions(s) if n == action)
        # describe the useful (goal-advancing) outcomes
        useful = []
        for p, ns in outs:
            new = ns.secured - s.secured
            for mid in new:
                useful.append({"mod": mid,
                               "text": self.by_id[mid].text[0] if self.by_id[mid].text else "",
                               "p": round(p, 4)})
        p_useful = sum(u["p"] for u in useful)
        return {
            "status": "step",
            "action": action,
            "cost_each": round(cost, 4),
            "p_useful": round(p_useful, 4),
            "expected_attempts": (round(1 / p_useful, 1) if p_useful > 0 else None),
            "useful_outcomes": useful,
            "expected_remaining_cost": round(exp_remaining, 2),
            "within_budget": (self.budget is None or
                              self.spent + exp_remaining <= self.budget),
            "state": {"rarity": s.rarity, "secured": sorted(s.secured),
                      "junk": [s.junk_pre, s.junk_suf]},
        }

    def apply_outcome(self, *, secured_add=None, secured_remove=None,
                      junk_pre_delta=0, junk_suf_delta=0,
                      new_rarity=None, paid=None):
        """Update the true state after you report what actually happened."""
        s = self.state
        sec = set(s.secured)
        if secured_add:
            sec |= ({secured_add} if isinstance(secured_add, str) else set(secured_add))
        if secured_remove:
            sec -= ({secured_remove} if isinstance(secured_remove, str) else set(secured_remove))
        self.state = State(new_rarity or s.rarity, frozenset(sec),
                           max(0, s.junk_pre + junk_pre_delta),
                           max(0, s.junk_suf + junk_suf_delta))
        if paid is not None:
            self.spent += paid


# --------------------------------------------------------------------------
def _simulate_cli():
    """Auto-simulated walkthrough (no input()) so it runs non-interactively."""
    import random
    raw = json.load(open("data/dagger_mods.json"))["mods"]
    class M:
        def __init__(s, d): s.__dict__.update(d)
        def weight_for(s, _): return s.weight
        def weight_for_tags(s, _): return s.weight
    mods = [M(d) for d in raw]
    by_id = {m.mod_id: m for m in mods}
    prices, league = _load_prices()
    ess = parse_essences("/home/claude/pob2/src/Data/Essence.lua")
    ess_prices = {"Essence of Flames": 4.5, "Essence of Haste": 7.0}

    wanted = ["LocalAddedFireDamage5", "LocalIncreasedAttackSpeed5"]
    sess = Session(mods, "dagger", 81, wanted, prices, budget=2000,
                   essences=ess, item_class="Dagger", essence_prices=ess_prices)
    print(f"Prices: {league} | budget: {sess.budget} ex")
    print("TARGET:", {w: by_id[w].text[0] for w in wanted}, "\n")

    step = 0
    while step < 30:
        info = sess.next_step()
        if info["status"] != "step":
            print(f"[{info['status'].upper()}] {info['msg']} (spent ~{info['spent']} ex)")
            break
        step += 1
        print(f"step {step}: {info['state']['rarity']:<6} "
              f"secured={info['state']['secured']} junk={info['state']['junk']}")
        print(f"   -> {info['action']}  (cost {info['cost_each']} ex each, "
              f"E[remaining]={info['expected_remaining_cost']} ex)")
        if info["useful_outcomes"]:
            print(f"   useful: {info['p_useful']*100:.1f}% "
                  f"(~{info['expected_attempts']} tries) -> "
                  f"{[u['mod'] for u in info['useful_outcomes']]}")

        # simulate the actual roll using the action's real probabilities
        sv, s = sess.solver, sess.state
        action = info["action"]
        outs = next(o for n, c, o in sv.actions(s) if n == action)
        cost = next(c for n, c, o in sv.actions(s) if n == action)
        r, acc, chosen = random.random(), 0.0, outs[-1][1]
        for p, ns in outs:
            acc += p
            if r <= acc:
                chosen = ns; break
        sess.state = chosen
        sess.spent += cost

    print(f"\nFinished in {step} steps, ~{sess.spent:.1f} ex spent "
          f"(optimal expected was {sess.E[State('Normal', frozenset(), 0, 0)]:.1f} ex).")


if __name__ == "__main__":
    _simulate_cli()
