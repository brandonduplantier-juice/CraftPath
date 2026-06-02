"""
probability.py
Core probability math for crafting steps.

Given an item state and the set of mods that an "add a modifier" action can
produce, the chance of landing a specific desired mod on one attempt is:

    p = weight(desired) / sum(weight(m) for m in addable pool)

The addable pool respects: item level gating, already-filled groups, and which
affix slot types are currently open. Expected attempts to land it is 1/p
(mean of a geometric distribution); probability within N attempts is
1 - (1 - p)^N.

NOTE: this models a uniform "add a random eligible mod" currency such as an
Exalted Orb on a rare. Currencies with different selection rules (essences =
forced mod, omens = constrained add, runes = unverified) are layered on top in
the methods module; they reuse these helpers but change which pool/weights apply.
"""
from __future__ import annotations
from dataclasses import dataclass


def addable_pool(state, mods):
    """Mods that an unconstrained add-action could currently place."""
    return [m for m in mods if state.can_add(m)]


def hit_probability(state, mods, target_mod_id) -> float:
    """P(landing target_mod_id) on a single unconstrained add action."""
    pool = addable_pool(state, mods)
    total = sum(m.weight_for(state.base_token) for m in pool)
    if total == 0:
        return 0.0
    tgt = next((m for m in pool if m.mod_id == target_mod_id), None)
    if tgt is None:                      # target not addable in this state
        return 0.0
    return tgt.weight_for(state.base_token) / total


def expected_attempts(p: float) -> float:
    return float("inf") if p <= 0 else 1.0 / p


def prob_within(p: float, n: int) -> float:
    return 0.0 if p <= 0 else 1.0 - (1.0 - p) ** n


@dataclass
class StepEstimate:
    target: str
    p: float
    exp_attempts: float
    p_in_10: float
    reachable: bool

    def __str__(self):
        if not self.reachable:
            return f"{self.target}: UNREACHABLE in this state (bricked for this target)"
        return (f"{self.target}: p={self.p:.4f}  "
                f"~{self.exp_attempts:.1f} attempts avg  "
                f"({self.p_in_10*100:.1f}% within 10)")


def estimate_step(state, mods, target_mod_id) -> StepEstimate:
    p = hit_probability(state, mods, target_mod_id)
    return StepEstimate(
        target=target_mod_id,
        p=p,
        exp_attempts=expected_attempts(p),
        p_in_10=prob_within(p, 10),
        reachable=p > 0,
    )
