"""Shared numerical kernels for the P0 methods. Pure NumPy/SciPy.

`pooled_two_sample` replaces `se = est / t_statistic` (Tier 1 fix, run log T1-P4-01). That form
returned NaN whenever the estimate was exactly 0. Methods refuse with `DEGENERATE_VARIANCE` when
this returns None; they never emit a NaN or infinite uncertainty.
"""
from __future__ import annotations

import math

import numpy as np
from scipy import stats


def pooled_two_sample(a: np.ndarray, b: np.ndarray, level: float) -> dict | None:
    """Equal-variance two-sample t on (a − b). Returns None when the pooled variance is zero."""
    na, nb = len(a), len(b)
    df = na + nb - 2
    sp2 = (float(np.sum((a - a.mean()) ** 2)) + float(np.sum((b - b.mean()) ** 2))) / df
    se = math.sqrt(sp2 * (1 / na + 1 / nb))
    if not se > 0 or not math.isfinite(se):
        return None
    est = float(a.mean() - b.mean())
    crit = float(stats.t.ppf(0.5 + level / 2, df))
    return {"estimate": est, "se": se, "df": df, "crit": crit, "p_value": float(2 * stats.t.sf(abs(est / se), df))}
