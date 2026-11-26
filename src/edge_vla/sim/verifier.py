"""Success verification: uses LIBERO's built-in BDDL ground-truth predicate
(no separate perception model needed) plus raw object coordinates so the
agent has data to reason about when replanning."""

from __future__ import annotations

from .sim_env import SimHandle


def check_success(sim: SimHandle) -> bool:
    return sim.check_success()


def detections(sim: SimHandle) -> dict:
    return sim.detections()
