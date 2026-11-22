"""The tests the monitor applies to a rate (README 3.4)."""
import numpy as np
import pytest
from scipy import stats as sps

from brain.stats import binomial_surprise, ratio_surprise, robust_scale


def test_binomial_surprise_is_the_exact_two_sided_tail():
    k, n, p = np.array([7.0]), np.array([20.0]), np.array([0.93])
    score, z = binomial_surprise(k, n, p)
    exact = min(1.0, 2 * sps.binom.cdf(7, 20, 0.93))
    assert score[0] == pytest.approx(-np.log10(exact), rel=1e-9)
    assert z[0] < -5  # far below the expected rate


def test_binomial_surprise_of_a_typical_hour_is_small_and_signed():
    score, z = binomial_surprise(np.array([94.0, 97.0]), np.array([100.0, 100.0]), np.array([0.94, 0.94]))
    assert score[0] < 1
    assert z[1] > 0


def test_binomial_surprise_does_not_underflow_for_a_huge_deviation():
    score, z = binomial_surprise(np.array([1000.0]), np.array([10000.0]), np.array([0.94]))
    assert np.isfinite(score[0]) and score[0] > 300
    assert np.isfinite(z[0]) and z[0] < -30


def test_a_rare_single_failure_is_not_a_surprise():
    # 9 of 10 against 0.995: the normal approximation calls this z = -4.3, the exact tail p = 0.05
    score, _ = binomial_surprise(np.array([9.0]), np.array([10.0]), np.array([0.995]))
    assert score[0] < 1.5


def test_no_denominator_gives_no_judgement():
    score, z = binomial_surprise(np.array([0.0, np.nan]), np.array([0.0, 5.0]), np.array([0.5, 0.5]))
    assert np.isnan(score).all() and np.isnan(z).all()


def test_ratio_surprise_scales_the_residual_by_the_history_spread():
    rng = np.random.default_rng(0)
    den = rng.integers(50, 500, 2000).astype(float)
    num = rng.poisson(den * 3.0).astype(float)
    scale = robust_scale((num - 3.0 * den) / np.sqrt(den))
    assert scale == pytest.approx(np.sqrt(3.0), rel=0.1)  # Poisson: variance of the residual = rate
    score, z = ratio_surprise(np.array([900.0, 300.0]), np.array([100.0, 100.0]), np.array([3.0, 3.0]),
                              np.array([scale, scale]))
    assert z[0] > 20 and score[0] > 50
    assert abs(z[1]) < 1e-9 and score[1] == pytest.approx(0.0, abs=1e-9)


def test_robust_scale_ignores_a_few_wild_values_and_needs_data():
    r = np.concatenate([np.random.default_rng(1).normal(0, 2.0, 500), [400.0, -900.0]])
    assert robust_scale(r) == pytest.approx(2.0, rel=0.15)
    assert np.isnan(robust_scale(np.array([1.0, 2.0])))
