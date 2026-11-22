"""Formula and breakdown checkers for validating metric graph edges on data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

import numpy as np
import pandas as pd

from graph.edges import GraphEdge
from graph.formulas import eval_formula, parse_formula
from graph.metric_graph import MetricGraph

ComputeFn = Callable[..., pd.DataFrame]
Outcome = Literal["pass", "fail", "skipped"]

_ADDITIVE_UNITS = frozenset({"count", "currency_eur", "currency"})


@dataclass(frozen=True)
class CheckResult:
    """Outcome of checking a formula or breakdown edge against data."""

    outcome: Outcome
    max_error: float | None
    checked: int
    detail: str


def _relative_error(expected: np.ndarray, actual: np.ndarray) -> np.ndarray:
    """Compute element-wise relative error, handling exact zeros cleanly."""
    diff = np.abs(actual - expected)
    denom = np.abs(expected)
    return np.divide(diff, denom, out=diff.copy(), where=denom > 0)


def check_formula(
    edge: GraphEdge,
    compute: ComputeFn,
    start: Any,
    end: Any,
    group_by: list[str] | None = None,
    rel_tol: float = 1e-4,
) -> CheckResult:
    """Verify that ``edge.formula`` holds on computed data within ``rel_tol``."""
    if edge.type != "formula" or not edge.formula:
        raise ValueError("check_formula requires a formula edge")

    lhs, inputs, compiled = parse_formula(edge.formula)
    key_cols = ["timestamp"] + (list(group_by) if group_by else [])

    lhs_df = compute(lhs, start, end, group_by=group_by)[key_cols + ["value"]].rename(
        columns={"value": "__lhs__"}
    )
    merged = lhs_df
    for inp in inputs:
        inp_df = compute(inp, start, end, group_by=group_by)[key_cols + ["value"]].rename(
            columns={"value": inp}
        )
        merged = merged.merge(inp_df, on=key_cols, how="inner")

    if merged.empty:
        return CheckResult("skipped", None, 0, "no overlapping rows")

    env = {inp: merged[inp].to_numpy(dtype=float) for inp in inputs}
    with np.errstate(divide="ignore", invalid="ignore"):
        rhs_vals = np.asarray(eval_formula(compiled, env), dtype=float)
    lhs_vals = merged["__lhs__"].to_numpy(dtype=float)

    valid = np.isfinite(lhs_vals) & np.isfinite(rhs_vals)
    checked = int(valid.sum())
    if checked == 0:
        return CheckResult("skipped", None, 0, "no finite comparable rows")

    rel_err = _relative_error(lhs_vals[valid], rhs_vals[valid])
    max_error = float(np.max(rel_err))
    pct = max_error * 100.0
    if max_error <= rel_tol:
        return CheckResult("pass", max_error, checked, f"{pct:.4g}% error, accepted")
    return CheckResult("fail", max_error, checked, f"{pct:.4g}% error > {rel_tol * 100:.4g}%")


def check_breakdown(
    edge: GraphEdge,
    metric_def: dict[str, Any],
    compute: ComputeFn,
    start: Any,
    end: Any,
    rel_tol: float = 1e-4,
) -> CheckResult:
    """Verify that slicing ``edge.to_metric`` by ``edge.dimension`` reconciles to total."""
    if edge.type != "breakdown" or not edge.dimension:
        raise ValueError("check_breakdown requires a breakdown edge")

    dim = edge.dimension
    if dim not in metric_def.get("dimensions", []):
        return CheckResult("fail", None, 0, f"dimension {dim!r} not in metric_def dimensions")

    total_df = compute(edge.to_metric, start, end, group_by=None)[["timestamp", "value"]].rename(
        columns={"value": "total"}
    )
    slice_df = compute(edge.to_metric, start, end, group_by=[dim])

    total_ts = set(total_df["timestamp"])
    slice_ts = set(slice_df["timestamp"])
    if total_ts != slice_ts:
        return CheckResult(
            "fail",
            None,
            0,
            f"timestamp mismatch: total={len(total_ts)} vs slices={len(slice_ts)}",
        )

    is_additive = metric_def.get("is_additive")
    if is_additive is None:
        is_additive = str(metric_def.get("unit", "")) in _ADDITIVE_UNITS

    if is_additive:
        recombined = slice_df.groupby("timestamp", as_index=False)["value"].sum().rename(
            columns={"value": "recombined"}
        )
    else:
        weight_metric = metric_def.get("weight_metric")
        if not weight_metric:
            return CheckResult("skipped", None, 0, "no weight_metric for non-additive breakdown")
        weight_df = compute(weight_metric, start, end, group_by=[dim]).rename(
            columns={"value": "weight"}
        )
        joined = slice_df.merge(weight_df, on=["timestamp", dim], how="inner")
        joined["weighted"] = joined["value"].astype(float) * joined["weight"].astype(float)
        grouped = joined.groupby("timestamp", as_index=False)[["weighted", "weight"]].sum()
        with np.errstate(divide="ignore", invalid="ignore"):
            grouped["recombined"] = grouped["weighted"] / grouped["weight"]
        recombined = grouped[["timestamp", "recombined"]]

    merged = total_df.merge(recombined, on="timestamp", how="inner")
    tot = merged["total"].to_numpy(dtype=float)
    rec = merged["recombined"].to_numpy(dtype=float)
    valid = np.isfinite(tot) & np.isfinite(rec)
    checked = int(valid.sum())
    if checked == 0:
        return CheckResult("skipped", None, 0, "no finite comparable rows")

    rel_err = _relative_error(tot[valid], rec[valid])
    max_error = float(np.max(rel_err))
    pct = max_error * 100.0
    if max_error <= rel_tol:
        return CheckResult("pass", max_error, checked, f"{pct:.4g}% error, accepted")
    return CheckResult("fail", max_error, checked, f"{pct:.4g}% error > {rel_tol * 100:.4g}%")


def check_graph(
    graph: MetricGraph,
    metric_defs: dict[str, dict[str, Any]],
    compute: ComputeFn,
    start: Any,
    end: Any,
    rel_tol: float = 1e-4,
) -> dict[tuple[str, str], CheckResult]:
    """Run all formula and breakdown checks in ``graph`` and update edge statuses."""
    results: dict[tuple[str, str], CheckResult] = {}
    seen: set[tuple[str, str]] = set()

    for edge in graph.edges():
        if edge.type not in {"formula", "breakdown"}:
            continue
        check_id = (edge.to_metric, edge.key)
        if check_id in seen:
            continue
        seen.add(check_id)

        if edge.type == "formula":
            res = check_formula(edge, compute, start, end, rel_tol=rel_tol)
        else:
            mdef = metric_defs.get(edge.to_metric, {})
            res = check_breakdown(edge, mdef, compute, start, end, rel_tol=rel_tol)

        graph.apply_result(edge, res.outcome, res.detail)
        results[check_id] = res

    return results
