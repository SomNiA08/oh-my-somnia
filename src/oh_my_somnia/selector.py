"""SELECT phase: natural selection over fitness results."""

from __future__ import annotations

from .evaluator import Fitness

EPSILON = 1e-9


def better(a: Fitness, b: Fitness) -> bool:
    """True when `a` is strictly fitter than `b`."""
    if a.passed != b.passed:
        return a.passed
    return a.score > b.score + EPSILON
