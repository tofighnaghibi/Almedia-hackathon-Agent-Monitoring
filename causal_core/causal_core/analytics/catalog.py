"""The P0 method catalog: one explicit table, no plugin discovery (v3.2 §1.2, §4.4; CP-017). The 12 P0 methods of §4.4, plus baseline_shift_v1 (CP-030) and the P1
counterfactual_rollout_v1 (Phase 7, user-authorized 2026-11-22; CP-035)."""
from __future__ import annotations

from .baseline import BASELINE
from .decomposition import KITAGAWA, RCD_LITE
from .descriptive import ASSOCIATION, DQ, IDENTITY, SEASONAL
from .experiments import MULTIPLICITY, RANDOMIZED
from .identification import IDENTIFIABILITY
from .quasi_experimental import DID
from .registry import Registry
from .sequential import SAVI
from .voi import VOI
from ..counterfactual.rollouts import ROLLOUT

P0_SPECS = (DQ, IDENTITY, SEASONAL, SAVI, KITAGAWA, RCD_LITE, ASSOCIATION, RANDOMIZED, MULTIPLICITY, DID, IDENTIFIABILITY, VOI, BASELINE, ROLLOUT)
REGISTRY = Registry(P0_SPECS)


def refusal_catalog() -> dict[str, tuple[str, ...]]:
    """Every typed refusal each method can return (plan §F deliverable 'method rejection catalog')."""
    return {s.id: s.refusal_codes for s in P0_SPECS}
