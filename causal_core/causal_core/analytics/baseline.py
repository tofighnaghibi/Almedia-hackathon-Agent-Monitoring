"""Learned, frozen baseline for change monitoring: a CURE/CUPED-style regression (user decision 2026-11-22; CP-030).

CUPED-style adjustment (with extra covariates) predicts each unit's outcome from pre-period data and analyses the
residual. The monitoring analogue: learn "normal" for a series as a regression on covariates over an OLDER, closed
training window, freeze it, and test whether the new points depart from it.

Model (v1): y_t = β0 + Σ_k β_k·[weekday(t) = k] + e_t, e_t iid N(0, σ²). Calendar covariates are computed from the
UTC timestamps (no domain input). Declared exogenous covariates (CURE's extra covariates, CUPAC-style predictions)
are a follow-up.

Why not just plug in the frozen fit: the fitted coefficients carry estimation error, and that error is the SAME
for every future point, a permanent offset. Measured: plugging it in gives 20% false alarms (8-week training) and
6% even with a year of training, at α = 5% over 200 days.

What this module does instead (exact under the model): under "no change", the monitored residuals r = y − X·β̂ are
jointly normal with covariance Σ = σ²(I + X (X_trᵀX_tr)⁻¹ Xᵀ), shared offset included. Under "level shift μ" the
mean is μ·1 with the same Σ. A normal mixture over μ ~ N(0, τ²σ²) gives the likelihood ratio
    log L_t = −½·log(1 + τ²σ²·v_t) + τ²σ²·u_t² / (2(1 + τ²σ²·v_t)),  u_t = 1ᵀΣ_t⁻¹r_t,  v_t = 1ᵀΣ_t⁻¹1,
a nonnegative test martingale under the null (a mixture of likelihood ratios), so Ville bounds false alarms by α.
It is two-sided and its power grows with data for any shift. Σ_t⁻¹ is applied by the Woodbury identity in O(p²).

Lower bounds (the user's "enforce a sensible lower bound"): at least 8 weeks of training (8 points per weekday),
and the training residuals must not be autocorrelated (lag-1 test); otherwise the method refuses.
σ is estimated from the training residuals (n − p df) and then treated as known: an approximation that the
measured Type-I (report §B3) covers for ≥ 8 weeks of training.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from .registry import CapabilitySpec, MethodResult, refused

BASELINE = CapabilitySpec(
    id="baseline_shift_v1", version="1", question_kinds=("DETECT_CHANGE",), input_shapes=("time_series", "state_trajectory"),
    output_kind="DESCRIPTIVE", estimand_families=None, required_metadata=("learned_baseline", "alpha", "family_size"),
    assumption_checks=("training_window_closed_before_monitoring", "training_length", "residual_independence",
                       "frozen_coefficients"),
    dependence_handling="refuses when training residuals are autocorrelated; the fit's shared error is in the null",
    max_work_units=1_000_000,
    refusal_codes=("BASELINE_TOO_SHORT", "BASELINE_AUTOCORRELATED", "BASELINE_OVERLAPS_MONITORING", "BAD_ALPHA",
                   "DEGENERATE_VARIANCE"),
    honest_conclusion="sequential alert against a learned, frozen calendar baseline; an alarm is not a cause")

MIN_TRAINING_DAYS = 56
DEFAULT_TAU = 0.5            # mixture scale in residual-SD units (measured: 0.25 valid but weaker; report §B3)
P = 7                        # intercept + 6 weekday indicators


def _features(ts: list[datetime]) -> np.ndarray:
    X = np.zeros((len(ts), P))
    X[:, 0] = 1.0
    for i, t in enumerate(ts):
        wd = t.weekday()
        if wd:
            X[i, wd] = 1.0
    return X


@dataclass(frozen=True)
class LearnedBaseline:
    """Frozen after learning. Everything needed to monitor later; nothing is re-estimated from monitored data."""
    coefficients: tuple[float, ...]
    gram: tuple[tuple[float, ...], ...]          # X_trᵀ X_tr
    residual_sd: float
    n_train: int
    train_start: datetime
    train_end: datetime
    lag1_autocorrelation: float
    features: str = "intercept + weekday indicators (UTC)"

    @property
    def problems(self) -> list[tuple[str, str]]:
        out = []
        if self.n_train < MIN_TRAINING_DAYS:
            out.append(("BASELINE_TOO_SHORT", f"{self.n_train} training points < {MIN_TRAINING_DAYS} (8 per weekday)"))
        if abs(self.lag1_autocorrelation) > 2.0 / math.sqrt(max(self.n_train, 1)):
            out.append(("BASELINE_AUTOCORRELATED", f"training residuals have lag-1 autocorrelation "
                        f"{self.lag1_autocorrelation:+.2f} (> 2/√n): increments would not be independent"))
        if not self.residual_sd > 0:
            out.append(("DEGENERATE_VARIANCE", "training residuals have zero variance"))
        return out


def learn_baseline(points: list[tuple[datetime, float]]) -> LearnedBaseline:
    """Fit on a closed training window. Pure; the caller freezes and registers the result (CP-023)."""
    pts = sorted((t, v) for t, v in points if v is not None)
    ts, y = [t for t, _ in pts], np.array([v for _, v in pts], dtype=float)
    X = _features(ts)
    G = X.T @ X
    if len(y) <= P or np.linalg.matrix_rank(G) < P:
        return LearnedBaseline(tuple([float("nan")] * P), tuple(tuple(r) for r in G.tolist()), 0.0, len(y),
                               ts[0] if ts else datetime.min, ts[-1] if ts else datetime.min, 0.0)
    b = np.linalg.solve(G, X.T @ y)
    res = y - X @ b
    sd = math.sqrt(float(res @ res) / (len(y) - P))
    r1 = float(np.corrcoef(res[:-1], res[1:])[0, 1]) if len(res) > 2 and res.std() > 0 else 0.0
    return LearnedBaseline(tuple(float(x) for x in b), tuple(tuple(float(x) for x in row) for row in G), sd, len(y),
                           ts[0], ts[-1], r1)


def mixture_log_wealth(baseline: LearnedBaseline, ts: list[datetime], y: list[float], tau: float = DEFAULT_TAU) -> np.ndarray:
    """log L_t for t = 1..T (see module docstring)."""
    b, G, s2 = np.array(baseline.coefficients), np.array(baseline.gram), baseline.residual_sd ** 2
    Xm = _features(ts)
    r = np.array(y, dtype=float) - Xm @ b
    t2 = tau * tau * s2
    out, Gm, Sx1, Sxr, n1, s1r = [], np.zeros((P, P)), np.zeros(P), np.zeros(P), 0.0, 0.0
    for t in range(len(r)):
        x = Xm[t]
        Gm += np.outer(x, x); Sx1 += x; Sxr += x * r[t]; n1 += 1.0; s1r += r[t]
        M = np.linalg.solve(G + Gm, np.column_stack([Sx1, Sxr]))
        v = (n1 - Sx1 @ M[:, 0]) / s2
        u = (s1r - Sx1 @ M[:, 1]) / s2
        out.append(-0.5 * math.log1p(t2 * v) + t2 * u * u / (2.0 * (1.0 + t2 * v)))
    return np.array(out)


def evaluate_baseline_shift(baseline: LearnedBaseline, points: list[tuple[datetime, float]], *, alpha: float,
                            family_size: int = 1, tau: float = DEFAULT_TAU) -> MethodResult:
    spec = BASELINE
    if not (0 < alpha < 1) or family_size < 1:
        return refused(spec, "BAD_ALPHA", "alpha must be in (0, 1) and the family non-empty", "DESCRIPTIVE")
    for code, why in baseline.problems:
        return refused(spec, code, why, "DESCRIPTIVE")
    pts = sorted((t, v) for t, v in points if v is not None)
    if pts and pts[0][0] <= baseline.train_end:
        return refused(spec, "BASELINE_OVERLAPS_MONITORING", f"monitored data starts {pts[0][0]:%Y-%m-%d}, on or before "
                       f"the end of training {baseline.train_end:%Y-%m-%d}: the baseline must be learned on older data",
                       "DESCRIPTIVE")
    a = alpha / family_size
    path = mixture_log_wealth(baseline, [t for t, _ in pts], [v for _, v in pts], tau)
    thr = math.log(1.0 / a)
    peak = float(path.max()) if len(path) else 0.0
    first = int(np.argmax(path >= thr)) if peak >= thr else None
    resid = [v - float(_features([t])[0] @ np.array(baseline.coefficients)) for t, v in pts]
    direction = None if first is None else ("down" if np.mean(resid[:first + 1]) < 0 else "up")
    return MethodResult(spec.id, spec.version, "OK", "DESCRIPTIVE",
                        {"log_wealth": float(path[-1]) if len(path) else 0.0, "peak_log_wealth": peak, "threshold": thr,
                         "alarm": peak >= thr, "first_crossing_index": first, "direction": direction, "alpha_per_stream": a,
                         "baseline": {"features": baseline.features, "n_train": baseline.n_train,
                                      "train_window": [baseline.train_start.isoformat(), baseline.train_end.isoformat()],
                                      "residual_sd": baseline.residual_sd, "lag1_autocorrelation": baseline.lag1_autocorrelation,
                                      "tau": tau}},
                        ("an alarm signals a departure from the learned calendar baseline, not a cause",),
                        (f"baseline learned on {baseline.n_train} points ending {baseline.train_end:%Y-%m-%d}, frozen",
                         f"training residual lag-1 autocorrelation {baseline.lag1_autocorrelation:+.2f}",
                         "shared estimation error of the frozen fit included in the null"),
                        ("the calendar model still describes the series (no structural break other than the shift)",
                         "Gaussian residuals with the training-window variance", "independence of residuals"))
