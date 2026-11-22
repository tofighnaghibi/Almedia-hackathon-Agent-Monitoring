"""L3 Graph Extension: validate candidate edges, resolve ambiguity, and extend the graph."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Literal, Protocol

import numpy as np
import pandas as pd

from graph.checkers import check_breakdown, check_formula
from graph.edges import GraphEdge
from graph.formulas import parse_formula
from graph.metric_graph import MetricGraph
from graph.registry import MetricDefinition

ComputeFn = Callable[..., pd.DataFrame]
Status = Literal["confirmed", "uncertain", "rejected"]


class MonitorRegistry(Protocol):
    """Minimal L4 monitoring registry interface."""

    # TODO: Replace with L4's concrete monitoring registry once implemented.
    def register(self, metric_name: str) -> None: ...


@dataclass(frozen=True)
class Candidate:
    """Proposed connection for a new metric from L3 candidate discovery."""

    new_metric: str
    type: str
    from_metric: str
    to_metric: str
    formula: str | None = None
    dimension: str | None = None
    lag_hours: int = 0
    source: str = "auto_discovered"
    rationale: str = ""


@dataclass
class ValidationResult:
    """Outcome of validating a single candidate edge against data."""

    candidate: Candidate
    edge: GraphEdge | None
    status: Status
    confidence: float
    evidence: str
    max_error: float | None = None
    failing_slices: list[str] = field(default_factory=list)
    suggested_checks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": asdict(self.candidate),
            "edge": self.edge.to_dict() if self.edge is not None else None,
            "status": self.status,
            "confidence": float(self.confidence),
            "evidence": self.evidence,
            "max_error": self.max_error,
            "failing_slices": list(self.failing_slices),
            "suggested_checks": list(self.suggested_checks),
        }


@dataclass
class ExtensionReport:
    """JSON-serializable summary of extending the graph with a new metric."""

    metric: str
    added_edges: list[dict[str, Any]]
    rejected_edges: list[dict[str, Any]]
    ambiguous_groups: list[list[str]]
    suggested_checks: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


def _metric_def_to_dict(metric_def: dict[str, Any] | MetricDefinition) -> dict[str, Any]:
    if isinstance(metric_def, MetricDefinition):
        return metric_def.to_dict()
    return dict(metric_def)


def _known_metrics(
    graph: MetricGraph,
    new_metric: str,
    metric_def: dict[str, Any] | MetricDefinition | None = None,
) -> set[str]:
    known = set(graph.graph.nodes) | {new_metric}
    if metric_def is not None:
        mdef = _metric_def_to_dict(metric_def)
        if "name" in mdef:
            known.add(str(mdef["name"]))
    return known


def _check_per_slice(
    edge: GraphEdge,
    compute: ComputeFn,
    start: Any,
    end: Any,
    rel_tol: float,
) -> list[str]:
    """Check a formula edge per country and per platform, returning failing slice labels."""
    failing: list[str] = []
    for dim in ("country", "platform"):
        try:
            probe = compute(edge.to_metric, start, end, group_by=[dim])
        except (KeyError, ValueError, TypeError):
            continue
        if dim not in probe.columns:
            continue
        for val in sorted(probe[dim].dropna().unique()):
            def _slice_compute(
                name: str, s: Any, e: Any, group_by: list[str] | None = None, _dim: str = dim, _val: Any = val
            ) -> pd.DataFrame:
                df = compute(name, s, e, group_by=[_dim])
                return df[df[_dim] == _val][["timestamp", "value"]].reset_index(drop=True)

            res = check_formula(edge, _slice_compute, start, end, rel_tol=rel_tol)
            if res.outcome == "fail":
                failing.append(f"{dim}={val}")
    return failing


def _safe_corr(x: np.ndarray, y: np.ndarray) -> float:
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 3:
        return 0.0
    xm, ym = x[mask], y[mask]
    if np.std(xm) == 0 or np.std(ym) == 0:
        return 0.0
    return float(np.corrcoef(xm, ym)[0, 1])


def _residualize(series: np.ndarray, driver: np.ndarray) -> np.ndarray:
    mask = np.isfinite(series) & np.isfinite(driver)
    if mask.sum() < 3 or np.std(driver[mask]) == 0:
        return series
    slope, intercept = np.polyfit(driver[mask], series[mask], 1)
    return series - (slope * driver + intercept)


def _validate_possible_cause(
    candidate: Candidate,
    edge: GraphEdge,
    compute: ComputeFn,
    start: Any,
    end: Any,
) -> ValidationResult:
    from_df = compute(candidate.from_metric, start, end).sort_values("timestamp")
    to_df = compute(candidate.to_metric, start, end).sort_values("timestamp")
    merged = from_df[["timestamp", "value"]].rename(columns={"value": "x"}).merge(
        to_df[["timestamp", "value"]].rename(columns={"value": "y"}),
        on="timestamp",
        how="inner",
    )
    if len(merged) < 4:
        return ValidationResult(candidate, edge, "rejected", 0.0, "insufficient rows for correlation")

    lag_center = max(0, int(candidate.lag_hours))
    candidate_lags = sorted({max(0, lag_center - 1), lag_center, lag_center + 1})

    best_lag = lag_center
    best_r = 0.0
    for lag in candidate_lags:
        x_shifted = merged["x"].shift(lag).to_numpy(dtype=float)
        y_vals = merged["y"].to_numpy(dtype=float)
        r = _safe_corr(x_shifted, y_vals)
        if abs(r) > abs(best_r):
            best_r = r
            best_lag = lag

    if abs(best_r) < 0.6:
        return ValidationResult(
            candidate,
            edge,
            "rejected",
            abs(best_r),
            f"weak lagged correlation r={best_r:.2f} at lag={best_lag}h",
        )

    # Traffic adjustment: remove the effect of sessions if available.
    r_adj = best_r
    try:
        sess_df = compute("sessions", start, end).sort_values("timestamp")
        with_sess = merged.merge(
            sess_df[["timestamp", "value"]].rename(columns={"value": "sessions"}),
            on="timestamp",
            how="inner",
        )
        if len(with_sess) >= 4:
            x_sh = with_sess["x"].shift(best_lag).to_numpy(dtype=float)
            y_v = with_sess["y"].to_numpy(dtype=float)
            s_v = with_sess["sessions"].to_numpy(dtype=float)
            r_adj = _safe_corr(_residualize(x_sh, s_v), _residualize(y_v, s_v))
    except (KeyError, ValueError):
        pass

    if abs(r_adj) < 0.3:
        return ValidationResult(
            candidate,
            edge,
            "uncertain",
            min(0.5, abs(r_adj)),
            f"explained by traffic (raw r={best_r:.2f}, traffic-adjusted r={r_adj:.2f})",
        )

    return ValidationResult(
        candidate,
        edge,
        "uncertain",
        float(abs(best_r)),
        f"lagged correlation r={best_r:.2f} at lag={best_lag}h (traffic-adjusted r={r_adj:.2f})",
    )


def validate_candidate(
    candidate: Candidate,
    graph: MetricGraph,
    compute: ComputeFn,
    start: Any,
    end: Any,
    metric_def: dict[str, Any] | MetricDefinition | None = None,
    rel_tol: float = 0.01,
) -> ValidationResult:
    """Validate a single candidate edge against data and return a ``ValidationResult``."""
    known = _known_metrics(graph, candidate.new_metric, metric_def)

    # Check metric names before constructing/evaluating anything.
    referenced = {candidate.from_metric, candidate.to_metric}
    if candidate.type == "formula":
        try:
            lhs, inputs, _ = parse_formula(candidate.formula or "")
        except ValueError as exc:
            return ValidationResult(candidate, None, "rejected", 0.0, str(exc))
        referenced |= {lhs, *inputs}

    unknown = sorted(m for m in referenced if m not in known)
    if unknown:
        return ValidationResult(
            candidate, None, "rejected", 0.0, f"unknown metric: {', '.join(unknown)}"
        )

    try:
        edge = GraphEdge(
            from_metric=candidate.from_metric,
            to_metric=candidate.to_metric,
            type=candidate.type,
            formula=candidate.formula,
            dimension=candidate.dimension,
            confidence=1.0 if candidate.type != "possible_cause" else 0.5,
            lag_hours=candidate.lag_hours,
            source=candidate.source,
            status="uncertain",
        )
    except ValueError as exc:
        return ValidationResult(candidate, None, "rejected", 0.0, str(exc))

    if candidate.type == "formula":
        res = check_formula(edge, compute, start, end, rel_tol=rel_tol)
        if res.outcome == "fail":
            pct = (res.max_error or 0.0) * 100.0
            return ValidationResult(
                candidate,
                edge,
                "rejected",
                0.0,
                f"{pct:.4g}% max error over {res.checked} rows (exceeds {rel_tol * 100:.2g}%)",
                max_error=res.max_error,
            )
        if res.outcome == "skipped":
            return ValidationResult(candidate, edge, "uncertain", 0.0, f"skipped: {res.detail}")

        max_err = res.max_error or 0.0
        conf = float(np.clip(1.0 - max_err / rel_tol, 0.5, 1.0))
        evidence = f"{max_err * 100:.4g}% max error over {res.checked} rows"
        failing_slices = _check_per_slice(edge, compute, start, end, rel_tol=rel_tol)
        if failing_slices:
            evidence = f"{evidence}; holds except {', '.join(failing_slices)}"
        return ValidationResult(
            candidate,
            edge,
            "confirmed",
            conf,
            evidence,
            max_error=max_err,
            failing_slices=failing_slices,
        )

    if candidate.type == "breakdown":
        mdef = _metric_def_to_dict(metric_def) if metric_def is not None else {}
        if candidate.dimension not in mdef.get("dimensions", []):
            return ValidationResult(
                candidate,
                edge,
                "rejected",
                0.0,
                f"dimension {candidate.dimension!r} not allowed for {candidate.to_metric}",
            )
        res = check_breakdown(edge, mdef, compute, start, end)
        if res.outcome == "fail":
            return ValidationResult(candidate, edge, "rejected", 0.0, res.detail, max_error=res.max_error)
        if res.outcome == "skipped":
            return ValidationResult(candidate, edge, "uncertain", 0.0, f"skipped: {res.detail}")
        conf = float(np.clip(1.0 - (res.max_error or 0.0) / 1e-4, 0.5, 1.0))
        return ValidationResult(
            candidate, edge, "confirmed", conf, res.detail, max_error=res.max_error
        )

    return _validate_possible_cause(candidate, edge, compute, start, end)


_RANK_RE = re.compile(r"(?:\brank\s*[:=]\s*|#)(\d+)\b", re.IGNORECASE)


def _extract_rank(rationale: str) -> int | None:
    """Extract numeric rank (1 = best) from candidate rationale if present."""
    match = _RANK_RE.search(rationale or "")
    return int(match.group(1)) if match else None


def _resolve_ambiguity_internal(
    results: list[ValidationResult],
) -> tuple[list[ValidationResult], list[list[str]], list[str]]:
    by_target: dict[str, list[ValidationResult]] = {}
    for res in results:
        if res.candidate.type == "formula" and res.status == "confirmed":
            by_target.setdefault(res.candidate.to_metric, []).append(res)

    ambiguous_groups: list[list[str]] = []
    suggested_checks: list[str] = []

    for target, group in by_target.items():
        if len(group) < 2:
            continue

        ranks = [_extract_rank(r.candidate.rationale) for r in group]
        ranked_only = [rk for rk in ranks if rk is not None]
        best_rank = min(ranked_only) if ranked_only else None
        winners = (
            [r for r, rk in zip(group, ranks) if rk == best_rank]
            if best_rank is not None
            else []
        )

        # If a single top-ranked candidate stands out (e.g. rank=1 vs rank=2+),
        # keep it confirmed and only mark the lower-ranked alternatives uncertain.
        if len(winners) == 1:
            winner = winners[0]
            winner.evidence = f"{winner.evidence}; top-ranked (rank={best_rank})"
            others = [r for r in group if r is not winner]
            for res in others:
                res.status = "uncertain"
                res.confidence = min(res.confidence, 0.6)
                res.evidence = f"{res.evidence}; ambiguous: also fits {winner.candidate.formula}"
            continue

        formulas = [r.candidate.formula or "" for r in group]
        ambiguous_groups.append(formulas)
        checks = [
            f"Compare per-slice errors for {target} across country/platform",
            f"Evaluate which formula for {target} separates during a fault period",
        ]
        suggested_checks.extend(checks)
        for res in group:
            others = [f for f in formulas if f != res.candidate.formula]
            res.status = "uncertain"
            res.confidence = min(res.confidence, 0.6)
            res.evidence = f"{res.evidence}; ambiguous: also fits {'; '.join(others)}"
            res.suggested_checks = list(checks)

    return results, ambiguous_groups, suggested_checks


def resolve_ambiguity(results: list[ValidationResult]) -> list[ValidationResult]:
    """Downgrade competing passing formulas for the same target to uncertain."""
    resolved, _, _ = _resolve_ambiguity_internal(results)
    return resolved


def extend_graph(
    graph: MetricGraph,
    metric_def: dict[str, Any] | MetricDefinition,
    candidates: list[Candidate],
    compute: ComputeFn,
    start: Any,
    end: Any,
    monitor_registry: MonitorRegistry | None = None,
) -> ExtensionReport:
    """Validate candidates, resolve ambiguities, update graph, and register metric."""
    mdef = _metric_def_to_dict(metric_def)
    metric_name = str(mdef["name"])

    raw_results = [
        validate_candidate(cand, graph, compute, start, end, metric_def=mdef)
        for cand in candidates
    ]
    resolved, ambiguous_groups, suggested_checks = _resolve_ambiguity_internal(raw_results)

    added_edges: list[dict[str, Any]] = []
    rejected_edges: list[dict[str, Any]] = []

    for res in resolved:
        if res.status == "rejected" or res.edge is None:
            rejected_edges.append(res.to_dict())
            continue

        res.edge.status = res.status
        res.edge.confidence = res.confidence
        res.edge.evidence = res.evidence
        res.edge.source = res.candidate.source
        graph.add_edge(res.edge)
        added_edges.append(res.edge.to_dict())

    if monitor_registry is not None:
        monitor_registry.register(metric_name)

    return ExtensionReport(
        metric=metric_name,
        added_edges=added_edges,
        rejected_edges=rejected_edges,
        ambiguous_groups=ambiguous_groups,
        suggested_checks=suggested_checks,
    )
