"""Finite capability registry and method result types (v3.2 §4.3; plan §F.1; CP-017).

Each method declares a CapabilitySpec. `Registry.assess()` runs the *generic* prechecks before
any method code: question kind, input shape, metadata, estimand family, work budget. Methods then
run their *specific* checks inside `evaluate()` and return a typed refusal when a premise fails. They
never silently reshape data (§4.3 conformance). Every assessment, accepted or refused, goes into the
trace that the ledger persists (`record_method_decision`).

Conformance with the §4.3 descriptor (Tier 1 completion, run log T1-P4-02):
* `cost_estimator_id` maps the input size to a forecast of work units, checked against the cap.
* `source_regimes` restricts the study regimes a method accepts (plan §F.1 "source regime").
  `None` means any regime: descriptive methods work on any data.
* An `Applicability` carries condition results, the missing premises, candidate next queries,
  the forecast and the cap, and the method's honest-conclusion ceiling (§4.4) as a scope limitation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

Status = Literal["SUPPORTED", "SUPPORTED_WITH_LIMITATIONS", "UNSUPPORTED"]
SourceRegime = Literal["randomized", "quasi_experimental", "observational", "descriptive"]

COST_ESTIMATORS = {
    "constant_v1": lambda size: 1,
    "linear_v1": lambda size: size,
    "n_log_n_v1": lambda size: max(1, int(size * max(1.0, math.log2(max(size, 2))))),
}


@dataclass(frozen=True)
class CapabilitySpec:
    id: str
    version: str
    question_kinds: tuple[str, ...]
    input_shapes: tuple[str, ...]
    output_kind: str
    estimand_families: tuple[str, ...] | None
    required_metadata: tuple[str, ...]
    assumption_checks: tuple[str, ...]
    dependence_handling: str
    max_work_units: int
    refusal_codes: tuple[str, ...]
    honest_conclusion: str
    source_regimes: tuple[str, ...] | None = None
    cost_estimator_id: str = "linear_v1"

    def __post_init__(self):
        if self.cost_estimator_id not in COST_ESTIMATORS:
            raise ValueError(f"{self.id}: unknown cost estimator {self.cost_estimator_id}")


@dataclass(frozen=True)
class MethodInput:
    """What the planner knows before running anything: shapes and metadata, not data values."""

    question_kind: str
    payload_shapes: tuple[str, ...]
    metadata: frozenset[str] = frozenset()
    estimand_functional: str | None = None
    work_units: int | None = 1          # input size fed to the cost estimator; None = unknown
    source_regime: str | None = None


@dataclass(frozen=True)
class Applicability:
    method_id: str
    method_version: str
    status: Status
    reasons: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    conditions: tuple[tuple[str, bool], ...] = ()
    next_queries: tuple[str, ...] = ()
    forecast_work_units: int | None = None
    work_unit_cap: int = 0
    scope_limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class MethodResult:
    """A typed outcome. REFUSED carries a refusal code; it is never a silent abstention (§4.2)."""

    method_id: str
    method_version: str
    status: Literal["OK", "OK_WITH_LIMITATIONS", "REFUSED"]
    claim_kind: str
    values: dict = field(default_factory=dict)
    limitations: tuple[str, ...] = ()
    assumptions_checked: tuple[str, ...] = ()
    assumptions_untestable: tuple[str, ...] = ()
    refusal_code: str | None = None
    refusal_reason: str | None = None


def refused(spec: CapabilitySpec, code: str, reason: str, claim_kind: str = "INCONCLUSIVE") -> MethodResult:
    if code not in spec.refusal_codes:
        raise ValueError(f"{spec.id}: refusal code {code} is not declared in its spec")
    return MethodResult(spec.id, spec.version, "REFUSED", claim_kind, refusal_code=code, refusal_reason=reason)


class Registry:
    def __init__(self, specs: tuple[CapabilitySpec, ...]):
        ids = [s.id for s in specs]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate capability id")
        self.specs = {s.id: s for s in specs}

    def assess(self, spec: CapabilitySpec, inp: MethodInput) -> Applicability:
        reasons, nxt, cond = [], [], []

        def check(name: str, ok: bool, reason: str = "", query: str = ""):
            cond.append((name, ok))
            if not ok:
                reasons.append(reason)
                if query:
                    nxt.append(query)

        check("question_kind", inp.question_kind in spec.question_kinds,
              f"QUESTION_KIND: {inp.question_kind} not in {list(spec.question_kinds)}")
        check("input_shape", bool(set(inp.payload_shapes) & set(spec.input_shapes)),
              f"INPUT_SHAPE: needs one of {list(spec.input_shapes)}, got {list(inp.payload_shapes)}",
              f"acquire a payload of shape {' or '.join(spec.input_shapes)}")
        missing = [m for m in spec.required_metadata if m not in inp.metadata]
        check("metadata", not missing, f"MISSING_METADATA: {missing}", f"declare {', '.join(missing)}")
        if spec.estimand_families is not None:
            check("estimand_family", inp.estimand_functional in spec.estimand_families,
                  f"ESTIMAND_FAMILY: {inp.estimand_functional} not in {list(spec.estimand_families)}",
                  f"restate the estimand as one of {list(spec.estimand_families)}, or choose another design")
        if spec.source_regimes is not None:
            check("source_regime", inp.source_regime in spec.source_regimes,
                  f"SOURCE_REGIME: {inp.source_regime} not in {list(spec.source_regimes)}",
                  f"evidence from a {' or '.join(spec.source_regimes)} source")
        forecast = None if inp.work_units is None else COST_ESTIMATORS[spec.cost_estimator_id](inp.work_units)
        check("budget", forecast is None or forecast <= spec.max_work_units,
              f"BUDGET: forecast {forecast} work units > cap {spec.max_work_units}", "narrow the window or sample")
        scope = [spec.honest_conclusion]
        if forecast is None:
            scope.append(f"input size unknown: the run is cut off at the cap of {spec.max_work_units} work units")
        status: Status = "UNSUPPORTED" if reasons else ("SUPPORTED_WITH_LIMITATIONS" if forecast is None else "SUPPORTED")
        return Applicability(spec.id, spec.version, status, tuple(reasons), tuple(missing), tuple(cond), tuple(nxt),
                             forecast, spec.max_work_units, tuple(scope))

    def select(self, inp: MethodInput) -> tuple[list[CapabilitySpec], list[Applicability]]:
        """(applicable specs, full trace including refusals). Never chooses a method by name alone."""
        trace = [self.assess(s, inp) for s in self.specs.values()]
        ok = [self.specs[a.method_id] for a in trace if a.status != "UNSUPPORTED"]
        return ok, trace
