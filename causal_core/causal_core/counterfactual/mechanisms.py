"""Fit and validate mechanisms (v3.2 §6.3; plan §I; CP-034, fixed 2026-11-22). Pure.

fit_mechanism: OLS of a target on DECLARED (parent, lag[, transform]) terms over a fitting window. The structure comes
from the caller (the graph); it is never searched here, so a missing equation is never invented.
validate_mechanism: one-step predictive intervals on a later, held-out window; PASS only if empirical coverage is
inside the 99% binomial band around the nominal level. Also records the held-out log score.
fit_shape_set: the straight-line equation and a curved variant (a squared term for each external parent), each
validated, weighted by held-out log score. Measured reason: a straight line validated one step ahead on curved worlds
gave 90% intervals that covered the true effect 29% of the time; widening by held-out error did not help (30%);
averaging the two shapes did (88%, and 95% on straight-line worlds).
backtest_set: a held-out multi-step backtest of the whole set feeding a target; its error ratio widens intervals
(never narrows them) and a gross failure blocks registration. It catches lag/dynamics errors that one-step checks miss.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta

import numpy as np
from scipy import stats

from ..contracts.mechanisms import MechanismSet, MechanismSpec

Series = dict[str, dict[datetime, float]]


def _terms(parents) -> list[tuple[str, int, str]]:
    return [(p[0], p[1], p[2] if len(p) > 2 else "identity") for p in parents]


def _rows(series: Series, target: str, terms, start: datetime, end: datetime, step: timedelta):
    X, y, raw, ts = [], [], [], []
    t = start
    while t <= end:
        yt = series.get(target, {}).get(t)
        xs = [series.get(v, {}).get(t - lag * step) for v, lag, _ in terms]
        if yt is not None and all(x is not None for x in xs):
            X.append([1.0] + [float(x) ** 2 if tr == "square" else float(x) for x, (_, _, tr) in zip(xs, terms)])
            raw.append([float(x) for x in xs]); y.append(float(yt)); ts.append(t)
        t += step
    k = 1 + len(terms)
    return np.array(X, dtype=float).reshape(-1, k), np.array(y, dtype=float), np.array(raw, dtype=float).reshape(-1, k - 1), ts


def fit_mechanism(series: Series, target: str, parents, *, mechanism_id: str, version: int, scope: dict,
                  lineage_roots: tuple[str, ...], start: datetime, end: datetime,
                  step: timedelta = timedelta(days=1)) -> MechanismSpec:
    terms = _terms(parents)
    X, y, raw, ts = _rows(series, target, terms, start, end, step)
    p = X.shape[1]
    if len(y) <= p + 2:
        raise ValueError(f"{target}: {len(y)} complete rows in the fitting window, need more than {p + 2}")
    scale = np.where(X.std(axis=0) > 0, X.std(axis=0), 1.0)
    if np.linalg.matrix_rank(X / scale) < p:
        raise ValueError(f"{target}: terms are collinear or constant in the fitting window: not identifiable")
    G = X.T @ X
    b = np.linalg.solve(G, X.T @ y)
    res = y - X @ b
    s2 = float(res @ res) / (len(y) - p)
    cov = s2 * np.linalg.inv(G)
    bounds, seen = [], set()
    for i, (v, lag, _) in enumerate(terms):
        if (v, lag) not in seen:
            seen.add((v, lag))
            bounds.append({"variable_id": v, "lag_steps": lag, "minimum": float(raw[:, i].min()), "maximum": float(raw[:, i].max())})
    return MechanismSpec.model_validate_json(json.dumps({
        "schema_version": "2.0", "mechanism_id": mechanism_id, "version": version, "target": target,
        "family": "linear_gaussian",
        "parents": [{"variable_id": v, "lag_steps": lag, "transform": tr} for v, lag, tr in terms],
        "coefficients": [float(x) for x in b], "covariance": [[float(x) for x in r] for r in cov],
        "noise_sd": math.sqrt(s2), "valid_regime": bounds, "feature_availability": "parents_observed_by_target_time",
        "fitted_on": {"scope": scope, "window": {"start": ts[0].isoformat(), "end": ts[-1].isoformat()}, "n": len(y),
                      "lineage_roots": list(lineage_roots)},
        "status": "CANDIDATE"}))


def validate_mechanism(spec: MechanismSpec, series: Series, *, start: datetime, end: datetime, level: float = 0.9,
                       min_points: int = 30, step: timedelta = timedelta(days=1)) -> MechanismSpec:
    """Held-out, one-step check. The window must start after the fitting window ends (no peeking).
    PASS when the empirical coverage lies inside the 99% binomial band around the nominal level for that many points."""
    if start <= spec.fitted_on.window.end:
        raise ValueError("validation window must start after the fitting window")
    terms = [(q.variable_id, q.lag_steps, q.transform) for q in spec.parents]
    X, y, raw, ts = _rows(series, spec.target, terms, start, end, step)
    bound = {(r.variable_id, r.lag_steps): (r.minimum, r.maximum) for r in spec.valid_regime}
    inside = np.ones(len(y), dtype=bool)
    for i, (v, lag, _) in enumerate(terms):
        lo, hi = bound[(v, lag)]
        inside &= (raw[:, i] >= lo) & (raw[:, i] <= hi)
    Xi, yi = X[inside], y[inside]
    b, cov = np.array(spec.coefficients), np.array(spec.covariance)
    pred = Xi @ b
    sd = np.sqrt(spec.noise_sd ** 2 + np.einsum("ij,jk,ik->i", Xi, cov, Xi))
    z = float(stats.norm.ppf(0.5 + level / 2))
    covered = float(np.mean(np.abs(yi - pred) <= z * sd)) if len(yi) else 0.0
    rmse = float(np.sqrt(np.mean((yi - pred) ** 2))) if len(yi) else 0.0
    band = 2.576 * math.sqrt(level * (1 - level) / max(len(yi), 1)) + 1.0 / max(len(yi), 1)
    ok = len(yi) >= min_points and abs(covered - level) <= band
    diag = {"holdout_window": {"start": (ts[0] if ts else start).isoformat(), "end": (ts[-1] if ts else end).isoformat()},
            "n": max(len(yi), 1), "rmse": rmse, "interval_level": level, "coverage": covered,
            "out_of_regime_points": int((~inside).sum()), "verdict": "PASS" if ok else "FAIL",
            "log_score": float(stats.norm.logpdf(yi, pred, sd).sum()) if len(yi) else None}
    body = spec.model_dump(mode="json")
    body.update(validation=diag, status="VALIDATED" if ok else "REJECTED")
    return MechanismSpec.model_validate_json(json.dumps(body))


def fit_shape_set(series: Series, target: str, parents, *, mechanism_id: str, version: int, scope: dict,
                  lineage_roots: tuple[str, ...], fit_start: datetime, fit_end: datetime, val_start: datetime,
                  val_end: datetime, step: timedelta = timedelta(days=1)) -> tuple[MechanismSet | None, list[MechanismSpec]]:
    """(set of VALIDATED shapes weighted by held-out log score, every candidate incl. rejected ones for the audit)."""
    base = _terms(parents)
    curved = base + [(v, lag, "square") for v, lag, tr in base if tr == "identity" and v != target]
    shapes = [("linear", base)] + ([("curved", curved)] if len(curved) > len(base) else [])
    candidates = []
    for name, terms in shapes:
        try:
            m = fit_mechanism(series, target, terms, mechanism_id=f"{mechanism_id}:{name}", version=version, scope=scope,
                              lineage_roots=lineage_roots, start=fit_start, end=fit_end, step=step)
        except ValueError:
            continue
        candidates.append(validate_mechanism(m, series, start=val_start, end=val_end, step=step))
    ok = [m for m in candidates if m.status == "VALIDATED" and m.validation.log_score is not None]
    if not ok:
        return None, candidates
    scores = np.array([m.validation.log_score for m in ok])
    w = np.exp(scores - scores.max())
    w = w / w.sum()
    return MechanismSet.model_validate_json(json.dumps({
        "schema_version": "2.0", "target": target, "members": [m.model_dump(mode="json") for m in ok],
        "weights": [float(x) for x in w], "weighting": "holdout_log_score"})), candidates


def backtest_set(mechanisms: dict, series: Series, target: str, *, start: datetime, end: datetime, horizon: int = 14,
                 level: float = 0.9, n_samples: int = 400, history_len: int = 7, step: timedelta = timedelta(days=1),
                 max_ratio: float = 3.0) -> MechanismSet:
    """Multi-step held-out backtest from every origin in [start, end − horizon]: forecast the target summed over the
    horizon with the OBSERVED exogenous inputs, compare with what happened. Inflation = max(1, RMS standardized error).
    FAIL (registration blocked) if the RMS standardized error exceeds max_ratio."""
    from .rollouts import members, rollout
    entry = mechanisms[target]
    if not isinstance(entry, MechanismSet):
        raise ValueError("backtest_set needs a MechanismSet for the target")
    nodes, stack = set(), [target]
    while stack:
        v = stack.pop()
        if v in nodes:
            continue
        nodes.add(v)
        if v in mechanisms:
            stack.extend(p.variable_id for m, _ in members(mechanisms[v]) for p in m.parents)
    exo = [v for v in nodes if v not in mechanisms]
    z = float(stats.norm.ppf(0.5 + level / 2))
    errs, hits, origin = [], 0, start
    while origin + (horizon - 1) * step <= end:
        hist = {v: [series[v][origin - k * step] for k in range(history_len, 0, -1) if (origin - k * step) in series.get(v, {})]
                for v in nodes}
        future = {v: [series[v].get(origin + k * step) for k in range(horizon)] for v in exo}
        actual = [series[target].get(origin + k * step) for k in range(horizon)]
        if all(x is not None for x in actual) and all(all(x is not None for x in f) for f in future.values()):
            p = rollout(mechanisms, target, hist, horizon, n_samples=n_samples, seed=int(origin.timestamp()) % 100_000,
                        level=level, exogenous_paths=future, apply_inflation=False)
            if p.status == "PREDICTED":
                sd = (p.baseline.upper - p.baseline.lower) / (2 * z)
                e = (sum(actual) - p.baseline.mean) / sd if sd > 0 else 0.0
                errs.append(e)
                hits += p.baseline.lower <= sum(actual) <= p.baseline.upper
        origin += step
    if not errs:
        raise ValueError("no complete backtest window in the held-out period")
    ratio = float(math.sqrt(np.mean(np.square(errs))))
    diag = {"horizon_steps": horizon, "origins": len(errs), "interval_level": level, "coverage": hits / len(errs),
            "inflation": max(1.0, ratio), "verdict": "PASS" if ratio <= max_ratio else "FAIL"}
    if diag["verdict"] == "FAIL":
        raise ValueError(f"{target}: multi-step forecasts miss by {ratio:.1f}× their stated uncertainty (> {max_ratio}); "
                         "not registered")
    body = entry.model_dump(mode="json")
    body["multi_step"] = diag
    return MechanismSet.model_validate_json(json.dumps(body))
