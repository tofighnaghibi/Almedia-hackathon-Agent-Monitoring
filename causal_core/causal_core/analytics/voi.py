"""Evidence acquisition ranking: a labelled heuristic, not calibrated VoI (v3.2 §4.6; plan §F; G14; CP-022).

proxy = discrimination × relevance × novelty × E[1 / (1 + latency_s + cost)]
* discrimination: the share of competing hypothesis pairs the option can tell apart
* novelty: 0 when the option only re-reads lineage roots already used (no replica inflation, G07)
* unknown latency or cost is NOT treated as free (CP-002 note). User decision 2026-11-22: an unknown value
  gets a geometric prior on {1, 2, ...} (seconds or cost units) whose mean is learned from the options in the
  same ranking with a known value (at least 1; 2 when none is known). The score takes the expectation over
  that prior, so an option with an unknown value scores at most half of an otherwise identical free one.
  Prior tails beyond 1 − 1e-9 are dropped, which can only lower the score.
* privacy above the allowed class: vetoed
* a non-executable option becomes an EvidenceGap recommendation and never blocks dispatch
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

import numpy as np

from .registry import CapabilitySpec

VOI = CapabilitySpec(
    id="voi_heuristic_v1", version="2", question_kinds=("DESIGN_EVIDENCE", "EXPLAIN_OBSERVATION", "ESTIMATE_EFFECT", "DETECT_CHANGE"),
    input_shapes=("query_options",), output_kind="ACTION_RECOMMENDATION", estimand_families=None, required_metadata=("hypotheses",),
    assumption_checks=("provider_support", "privacy", "cost_known"), dependence_handling="not inferential",
    max_work_units=10_000, refusal_codes=("NO_HYPOTHESES",),
    honest_conclusion='heuristic ranking of diagnostic value; not a calibrated expected information gain', cost_estimator_id='n_log_n_v1')
PRIVACY_ORDER = ("public", "internal", "confidential", "restricted")
UNKNOWN_PRIOR_MEAN = 2.0      # geometric prior mean when no option in the ranking has a known value
GEOMETRIC_TAIL = 1e-9
GEOMETRIC_MAX_TERMS = 4096


@dataclass(frozen=True)
class QueryOption:
    option_id: str
    separates: frozenset[frozenset[str]]       # hypothesis pairs this option can distinguish
    relevance: float
    lineage_roots: frozenset[str]
    executable: bool
    privacy: str = "internal"
    latency_ms: float | None = None
    cost_units: float | None = None
    recommendation: str = ""


@dataclass(frozen=True)
class Ranking:
    ranked: tuple[tuple[str, float, tuple[str, ...]], ...]
    vetoed: tuple[tuple[str, str], ...]
    evidence_gaps: tuple[tuple[str, str], ...]
    label: str = field(default="PROXY_SCORE (heuristic; not a calibrated expected value of information)")


def geometric_pmf(mean: float) -> np.ndarray:
    """P(X = k) for k = 1, 2, ... with E[X] = mean (≥ 1), truncated where the remaining tail is ≤ 1e-9."""
    p = 1.0 / max(1.0, float(mean))
    if p >= 1.0:
        return np.array([1.0])
    q = 1.0 - p
    n = min(GEOMETRIC_MAX_TERMS, max(1, int(np.ceil(np.log(GEOMETRIC_TAIL) / np.log(q)))))
    return p * q ** np.arange(n)


def expected_inverse_cost(known: float, unknown_means: list[float]) -> float:
    """E[1 / (1 + known + U)] where U is the sum of independent geometric priors (one per unknown field)."""
    if not unknown_means:
        return 1.0 / (1.0 + known)
    pmf, offset = np.array([1.0]), 0
    for m in unknown_means:
        pmf, offset = np.convolve(pmf, geometric_pmf(m)), offset + 1      # each geometric starts at 1
    support = offset + np.arange(len(pmf))
    return float(np.sum(pmf / (1.0 + known + support)))


def _learned_mean(values: list[float]) -> tuple[float, int]:
    return (max(1.0, float(np.mean(values))), len(values)) if values else (UNKNOWN_PRIOR_MEAN, 0)


def rank(options: list[QueryOption], hypotheses: list[str], seen_roots: frozenset[str], max_privacy: str = "internal") -> Ranking:
    if len(hypotheses) < 2:
        raise ValueError("NO_HYPOTHESES: ranking needs at least two competing hypotheses")
    pairs = {frozenset(p) for p in combinations(sorted(hypotheses), 2)}
    ranked, vetoed, gaps = [], [], []
    usable = [o for o in options if o.executable and PRIVACY_ORDER.index(o.privacy) <= PRIVACY_ORDER.index(max_privacy)]
    lat_mean, lat_n = _learned_mean([o.latency_ms / 1000 for o in usable if o.latency_ms is not None])
    cost_mean, cost_n = _learned_mean([o.cost_units for o in usable if o.cost_units is not None])
    for o in options:
        if PRIVACY_ORDER.index(o.privacy) > PRIVACY_ORDER.index(max_privacy):
            vetoed.append((o.option_id, f"PRIVACY_VETO: {o.privacy} > {max_privacy}"))
            continue
        disc = len(o.separates & pairs) / len(pairs)
        if not o.executable:
            if disc > 0:
                gaps.append((o.option_id, o.recommendation or "not executable by any provider: recommend as a measurement/test"))
            continue
        novelty = 0.0 if o.lineage_roots and o.lineage_roots <= seen_roots else 1.0
        notes, unknown = [], []
        if o.latency_ms is None:
            unknown.append(lat_mean)
            notes.append(f"latency UNKNOWN: geometric prior, mean {lat_mean:g} s (learned from {lat_n} option(s) with known latency)")
        if o.cost_units is None:
            unknown.append(cost_mean)
            notes.append(f"cost UNKNOWN: geometric prior, mean {cost_mean:g} units (learned from {cost_n} option(s) with known cost)")
        known = (o.latency_ms or 0) / 1000 + (o.cost_units or 0)
        inv = expected_inverse_cost(known, unknown)
        if unknown:
            notes.append(f"expected downweight vs. a free option: ×{inv * (1 + known):.3f}")
        if novelty == 0.0:
            notes.append("only re-reads lineage already used: novelty 0")
        score = disc * o.relevance * novelty * inv
        ranked.append((o.option_id, round(score, 6), tuple(notes)))
    ranked.sort(key=lambda r: -r[1])
    return Ranking(tuple(ranked), tuple(vetoed), tuple(gaps))
