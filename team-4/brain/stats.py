"""How surprising a rate is, given how many chances it had.

A rate is judged on its counts, never on the ratio alone: 7 successes in 20 attempts and 700 in
2000 are the same ratio and very different evidence.

- A proportion (the numerator can never exceed the denominator) gets the exact two-sided binomial
  tail. The normal approximation is wrong where it matters here: for a rate near 1 and a handful
  of attempts it calls one ordinary failure a 4-sigma event.
- Any other ratio (impressions per session, EUR per order) gets the residual `numerator - rate *
  denominator`, scaled by sqrt(denominator) and by the spread that residual showed in clean history.

Both return (surprise, z): surprise is -log10 of the two-sided p-value, z a signed normal-equivalent
for display. Everything is vectorised and NaN where no judgement is possible.
"""
from __future__ import annotations

import numpy as np
from scipy import stats as sps

LN10 = np.log(10.0)
MIN_SCALE_SAMPLES = 24


def _z_from_log_tail(log_tail: np.ndarray, sign: np.ndarray) -> np.ndarray:
    """Normal-equivalent of a one-sided tail probability given as a logarithm."""
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        exact = sps.norm.isf(np.exp(np.clip(log_tail, -700.0, 0.0)))
        far = np.sqrt(np.maximum(-2.0 * log_tail, 0.0))  # leading term of the normal tail, for underflow
    return sign * np.where(log_tail < -700.0, far, np.maximum(exact, 0.0))


def binomial_surprise(k, n, p):
    """Exact two-sided binomial tail of k successes in n trials against rate p."""
    k, n, p = (np.asarray(a, dtype=float) for a in (k, n, p))
    ok = np.isfinite(k) & np.isfinite(n) & np.isfinite(p) & (n > 0) & (p > 0) & (p < 1)
    kk, nn, pp = np.where(ok, k, 0.0), np.where(ok, n, 1.0), np.where(ok, p, 0.5)
    kk = np.clip(np.round(kk), 0, np.round(nn))
    with np.errstate(divide="ignore", invalid="ignore"):
        lower = sps.binom.logcdf(kk, np.round(nn), pp)       # P(X <= k)
        upper = sps.binom.logsf(kk - 1, np.round(nn), pp)    # P(X >= k)
    tail = np.minimum(lower, upper)
    # far beyond double precision the exact tail underflows; the Chernoff bound -n * KL(k/n || p)
    # keeps such cases finite and ordered by how extreme they are
    q = np.clip(kk / np.round(nn), 1e-12, 1 - 1e-12)
    chernoff = -np.round(nn) * (q * np.log(q / pp) + (1 - q) * np.log((1 - q) / (1 - pp)))
    tail = np.where(np.isfinite(tail), tail, chernoff)
    surprise = np.maximum(-(tail + np.log(2.0)) / LN10, 0.0)
    z = _z_from_log_tail(np.minimum(tail + np.log(2.0), 0.0) - np.log(2.0), np.where(upper < lower, 1.0, -1.0))
    z = np.where(np.isclose(kk, nn * pp), 0.0, z)
    return np.where(ok, surprise, np.nan), np.where(ok, z, np.nan)


def robust_scale(residuals) -> float:
    """Spread of residuals in clean history: the larger of 1.4826 x median absolute deviation and
    the standard deviation of the middle 98 %. The first ignores a few wild hours; the second keeps
    lumpy counts honest, where most hours sit exactly on the median and the deviation alone would be
    far too small. NaN when there is too little history or no spread at all."""
    r = np.asarray(residuals, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < MIN_SCALE_SAMPLES:
        return float("nan")
    mad = 1.4826 * float(np.median(np.abs(r - np.median(r))))
    lo, hi = np.quantile(r, [0.01, 0.99])
    scale = max(mad, float(np.std(np.clip(r, lo, hi))))
    return scale if scale > 0 else float("nan")


def scaled_residual(num, den, rate):
    num, den, rate = (np.asarray(a, dtype=float) for a in (num, den, rate))
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(den > 0, (num - rate * den) / np.sqrt(den), np.nan)


def ratio_surprise(num, den, rate, scale):
    """Residual of a ratio against its expected rate, in units of its historical spread."""
    scale = np.asarray(scale, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = scaled_residual(num, den, rate) / scale
    ok = np.isfinite(z)
    zz = np.where(ok, z, 0.0)
    surprise = np.maximum(-(sps.norm.logsf(np.abs(zz)) + np.log(2.0)) / LN10, 0.0)
    return np.where(ok, surprise, np.nan), np.where(ok, z, np.nan)
