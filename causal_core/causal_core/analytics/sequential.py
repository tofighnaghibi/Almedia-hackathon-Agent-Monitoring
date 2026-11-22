"""Sequential monitoring e-process (v3.2 §4.4 savi_eprocess_v1; plan §F.3; C03; CP-018).

Declared null: z_t are independent N(0, 1) (or conditionally 1-sub-Gaussian) under H0.

Betting rule (user decision 2026-11-22): a *regularized predictable plug-in* bet, two-sided by default.
  up-bet   λ⁺_t = clip((k·λ0 + Σ_{s<t} z_s) / (k + t − 1), floor, cap)
  down-bet λ⁻_t = the same on −z
  log E^±_t += λ^±_t (±z_t) − (λ^±_t)²/2
  two-sided wealth E_t = (E⁺_t + E⁻_t) / 2
* λ0 = 0.25 is the starting bet; k (prior_strength) is the regularization: the prior counts as k observations.
  k → ∞ is the fixed bet λ0; k → 0 is the pure plug-in. Default k = 100 (measured; docs/phase4/STATISTICS_REPORT.md §B).
* floor 0.05 keeps a minimum bet, so shifts above floor/2 have positive long-run drift; cap 1.0.
Validity: each λ_t uses only data before t, so each side is a nonnegative test martingale under H0, and so is
their average. Ville's inequality bounds P(sup E_t ≥ 1/α) ≤ α. Averaging the two sides is never less powerful
than giving each side α/2.

The approved Phase 0 reference D5 (fixed λ = 0.5, one-sided; fixture 08) stays available as bet="fixed".
Multiple streams: α is split across the declared family (Bonferroni over streams).
Fail-closed: if the stream's dependence is not declared independent, the method refuses (AR(1) noise inflates
false alarms far above α; report §B).
Revisions and replicas are handled by the ledger's consumption rules (storage/eprocess_state.py, CP-015): the
path is recomputed from the consumed set, so a revised early value also changes every later bet.
"""
from __future__ import annotations

import math
from typing import Callable

import numpy as np

from .registry import CapabilitySpec, MethodResult, refused

SAVI = CapabilitySpec(
    id="savi_eprocess_v1", version="2", question_kinds=("DETECT_CHANGE",), input_shapes=("time_series", "aggregate_stats", "state_trajectory"),
    output_kind="DESCRIPTIVE", estimand_families=None, required_metadata=("null_spec", "dependence", "alpha", "family_size"),
    assumption_checks=("declared_null", "predictable_bet", "independence", "alpha_budget"),
    dependence_handling="refuses unless increments are declared independent", max_work_units=1_000_000,
    refusal_codes=("DEPENDENCE_UNSUPPORTED", "NULL_UNDECLARED", "BAD_ALPHA", "BAD_BET"),
    honest_conclusion='sequential alert only under the documented e-process conditions; an alarm is not a cause')

FIXED_LAMBDA = 0.5                # Phase 0 reference D5 (fixture 08)
DEFAULT_LAMBDA0 = 0.25            # user decision 2026-11-22: start at 0.25
DEFAULT_PRIOR_STRENGTH = 100.0    # measured trade-off, report §B
LAMBDA_FLOOR = 0.05
LAMBDA_CAP = 1.0
BETS = ("regularized", "fixed")
SIDES = ("two", "up", "down")


def bet_sizes(z: np.ndarray, *, sign: int = 1, lam0: float = DEFAULT_LAMBDA0, prior_strength: float = DEFAULT_PRIOR_STRENGTH,
              lam_floor: float = LAMBDA_FLOOR, lam_cap: float = LAMBDA_CAP) -> np.ndarray:
    """Predictable bets for each step (last axis = time). Step t uses only z before t."""
    z = np.asarray(z, dtype=float) * sign
    if math.isinf(prior_strength):
        return np.full_like(z, lam0)
    s_prev = np.cumsum(z, axis=-1) - z
    n_prev = np.arange(z.shape[-1], dtype=float)
    denom = prior_strength + n_prev
    safe = np.where(denom > 0, denom, 1.0)
    raw = np.where(denom > 0, (prior_strength * lam0 + s_prev) / safe, lam0)
    return np.clip(raw, lam_floor, lam_cap)


def log_wealth_path(z, *, bet: str = "regularized", sides: str = "two", lam: float = FIXED_LAMBDA,
                    lam0: float = DEFAULT_LAMBDA0, prior_strength: float = DEFAULT_PRIOR_STRENGTH,
                    lam_floor: float = LAMBDA_FLOOR, lam_cap: float = LAMBDA_CAP, return_sides: bool = False):
    """Log-wealth after each step (last axis = time). Works on one stream or a (streams × peeks) array."""
    z = np.asarray(z, dtype=float)

    def side(sign: int) -> np.ndarray:
        if bet == "fixed":
            lam_t = np.full_like(z, lam)
        else:
            lam_t = bet_sizes(z, sign=sign, lam0=lam0, prior_strength=prior_strength, lam_floor=lam_floor, lam_cap=lam_cap)
        return np.cumsum(lam_t * sign * z - lam_t * lam_t / 2.0, axis=-1)

    up = side(1) if sides in ("two", "up") else None
    down = side(-1) if sides in ("two", "down") else None
    if sides == "two":
        total = np.logaddexp(up, down) - math.log(2.0)
    else:
        total = up if up is not None else down
    return (total, up, down) if return_sides else total


def path_fn(**bet) -> Callable[[list[float]], list[float]]:
    """The bet as a whole-path function, for the ledger's revision-aware recompute (storage/eprocess_state.py)."""
    return lambda zs: [float(x) for x in log_wealth_path(np.asarray(zs, dtype=float), **bet)] if zs else []


def increment(lam: float = FIXED_LAMBDA):
    """The fixed bet as a per-step log-wealth increment (Phase 0 reference D5; fixture 08)."""
    return lambda z: lam * z - lam * lam / 2.0


def _bet_problem(bet, sides, lam, lam0, prior_strength, lam_floor, lam_cap) -> str | None:
    if bet not in BETS:
        return f"unknown bet {bet!r}; expected one of {BETS}"
    if sides not in SIDES:
        return f"unknown sides {sides!r}; expected one of {SIDES}"
    if bet == "fixed":
        return None if (math.isfinite(lam) and lam > 0) else "fixed bet λ must be a positive finite number"
    if not (math.isfinite(lam_floor) and math.isfinite(lam_cap) and 0 < lam_floor <= lam0 <= lam_cap):
        return f"need 0 < floor ≤ λ0 ≤ cap (got {lam_floor}, {lam0}, {lam_cap})"
    if not prior_strength >= 0:
        return "prior_strength (regularization) must be ≥ 0"
    return None


def evaluate_eprocess(z: list[float], *, null_spec: str | None, dependence: str, alpha: float, family_size: int = 1,
                      bet: str = "regularized", sides: str = "two", lam: float = FIXED_LAMBDA,
                      lam0: float = DEFAULT_LAMBDA0, prior_strength: float = DEFAULT_PRIOR_STRENGTH,
                      lam_floor: float = LAMBDA_FLOOR, lam_cap: float = LAMBDA_CAP) -> MethodResult:
    spec = SAVI
    if not null_spec:
        return refused(spec, "NULL_UNDECLARED", "an e-process needs a declared null distribution", "DESCRIPTIVE")
    if dependence != "independent":
        return refused(spec, "DEPENDENCE_UNSUPPORTED", f"dependence {dependence!r}: the test martingale is not valid; "
                       "Type-I control would not hold", "DESCRIPTIVE")
    if not (0 < alpha < 1) or family_size < 1:
        return refused(spec, "BAD_ALPHA", "alpha must be in (0, 1) and the family non-empty", "DESCRIPTIVE")
    problem = _bet_problem(bet, sides, lam, lam0, prior_strength, lam_floor, lam_cap)
    if problem:
        return refused(spec, "BAD_BET", problem, "DESCRIPTIVE")
    cfg = {"bet": bet, "sides": sides}
    cfg.update({"lambda": lam} if bet == "fixed" else
               {"lambda0": lam0, "prior_strength": prior_strength, "floor": lam_floor, "cap": lam_cap})
    a = alpha / family_size
    path, up, down = log_wealth_path(z, bet=bet, sides=sides, lam=lam, lam0=lam0, prior_strength=prior_strength,
                                     lam_floor=lam_floor, lam_cap=lam_cap, return_sides=True)
    threshold = math.log(1.0 / a)
    peak = float(path.max()) if len(path) else 0.0
    first = int(np.argmax(path >= threshold)) if peak >= threshold else None
    direction = None
    if first is not None:
        if sides == "two":
            direction = "up" if up[first] >= down[first] else "down"
        else:
            direction = sides
    bet_text = (f"fixed bet λ = {lam}" if bet == "fixed" else
                f"regularized predictable bet (λ0 = {lam0}, prior strength {prior_strength:g}, floor {lam_floor}, cap {lam_cap})")
    return MethodResult(spec.id, spec.version, "OK", "DESCRIPTIVE",
                        {"log_wealth": float(path[-1]) if len(path) else 0.0, "peak_log_wealth": peak,
                         "threshold": threshold, "alarm": peak >= threshold, "first_crossing_index": first,
                         "direction": direction, "alpha_per_stream": a, "bet_config": cfg},
                        ("an alarm signals evidence against the declared null, not a cause",),
                        ("null declared", bet_text + ("; two-sided mixture" if sides == "two" else f"; one-sided ({sides})"),
                         "independence declared"),
                        ("the declared null actually holds before any change", "independence of increments"))


def type_one_error(n_streams: int, n_peeks: int, *, rho: float = 0.0, alpha: float = 0.05, seed: int = 7, **bet) -> float:
    """Fraction of null streams that ever alarm. rho > 0 gives AR(1) noise with unit marginal variance."""
    rng = np.random.default_rng(seed)
    eps = rng.standard_normal((n_streams, n_peeks))
    z = np.empty_like(eps)
    z[:, 0] = eps[:, 0]
    for t in range(1, n_peeks):
        z[:, t] = rho * z[:, t - 1] + math.sqrt(1 - rho * rho) * eps[:, t]
    return float((log_wealth_path(z, **bet).max(axis=1) >= math.log(1.0 / alpha)).mean())
