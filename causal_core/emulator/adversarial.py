"""Adversarial causal scenarios (plan §L "causal adversarial"; roadmap Phase 4 emulator gains).

Each returns a Draw: visible rows + hidden truth. These are cases where a naive pipeline is
confidently wrong. The evaluation harness scores the core's behaviour against the truth.
The core never sees it.
"""
from __future__ import annotations

import random

from .scenarios import Draw, Table, _r

V = "emulator/0.3:adversarial"


def pretrend_ok_but_violated(seed: int = 41, n_per_group: int = 20, shock: float = 1.0) -> Draw:
    """No treatment effect, parallel pre-trends, but a concurrent shock hits only treated units post-intervention."""
    rng = random.Random(seed)
    rows = []
    for group in ("treated", "control"):
        for k in range(n_per_group):
            alpha = rng.gauss(10.0, 0.5)
            for t in (-3, -2, -1, 0):
                y = alpha + 0.5 * t + (shock if group == "treated" and t >= 0 else 0.0) + rng.gauss(0, 0.3)
                rows.append((f"{group[0]}{k:02d}", group, t, _r(y)))
    return Draw(Table(("unit", "group", "period", "y"), ("str", "str", "int", "float"), tuple(rows)),
                {"generator": f"{V}:pretrend_ok_but_violated", "seed": seed, "true_values": {"att": 0.0},
                 "violation": "a concurrent post-period shock to treated units only; pre-trends are parallel"})


def differential_attrition(seed: int = 42, n: int = 4000, effect: float = 0.2) -> Draw:
    """Randomized; treated units with low outcomes drop out more often → complete-case ITT biased upward."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        arm = "treatment" if rng.random() < 0.5 else "control"
        y = (effect if arm == "treatment" else 0.0) + rng.gauss(0, 1)
        drop = rng.random() < (0.45 if (arm == "treatment" and y < 0) else 0.08)
        rows.append((f"u{i:05d}", arm, None if drop else _r(y)))
    return Draw(Table(("unit", "arm", "y"), ("str", "str", "float"), tuple(rows)),
                {"generator": f"{V}:differential_attrition", "seed": seed, "true_values": {"itt": effect},
                 "violation": "missing not at random, more in the treatment arm"})


def spillover(seed: int = 43, sites: int = 200, per_site: int = 10, effect: float = 0.5, spill: float = 0.5) -> Draw:
    """Units randomized within sites; controls sharing a site with treated units get spill × effect."""
    rng = random.Random(seed)
    rows = []
    for s in range(sites):
        arms = ["treatment" if rng.random() < 0.5 else "control" for _ in range(per_site)]
        share_treated = arms.count("treatment") / per_site
        for j, arm in enumerate(arms):
            y = (effect if arm == "treatment" else spill * effect * share_treated) + rng.gauss(0, 1)
            rows.append((f"s{s:03d}u{j}", arm, _r(y)))
    return Draw(Table(("unit", "arm", "y"), ("str", "str", "float"), tuple(rows)),
                {"generator": f"{V}:spillover", "seed": seed, "true_values": {"direct_effect": effect},
                 "violation": "interference between units in the same site (SUTVA fails)"})


def skewed_clusters(seed: int = 44, clusters_per_arm: int = 6, effect: float = 0.3) -> Draw:
    """Cluster-randomized with very unequal cluster sizes."""
    rng = random.Random(seed)
    rows, n = [], 0
    for arm in ("treatment", "control"):
        for c in range(clusters_per_arm):
            size = rng.choice([5, 10, 20, 60, 200])
            u = rng.gauss(0, 0.5)
            for _ in range(size):
                rows.append((f"u{n:05d}", arm, f"{arm[0]}c{c}", _r((effect if arm == "treatment" else 0.0) + u + rng.gauss(0, 1))))
                n += 1
    return Draw(Table(("unit", "arm", "cluster", "y"), ("str", "str", "str", "float"), tuple(rows)),
                {"generator": f"{V}:skewed_clusters", "seed": seed, "true_values": {"itt": effect},
                 "violation": "few, very unequal clusters"})


def srm_history(seed: int = 45, n_experiments: int = 2000, healthy: float = 0.90, gross: float = 0.05,
                gross_share: float = 0.47, subtle_share: float = 0.4925) -> Draw:
    """A platform's past experiments, for learning the SRM warning threshold (user decision 2026-11-22).

    Visible: per experiment, the units counted in each arm of a declared 50/50 split.
    Truth: which experiments really had broken assignment. 'gross' bugs (true share 0.47) are blatant; the
    rest of the broken ones are 'subtle' (true share 0.4925) and mostly land between p = 0.001 and 0.05.
    """
    rng = random.Random(seed)
    rows, broken = [], []
    for i in range(n_experiments):
        n = rng.randint(4_000, 40_000)
        u = rng.random()
        kind, share = ("healthy", 0.5) if u < healthy else (("gross", gross_share) if u < healthy + gross else ("subtle", subtle_share))
        t = _binomial(rng, n, share)
        rows.append((f"exp{i:04d}", t, n - t))
        if kind != "healthy":
            broken.append(f"exp{i:04d}")
    return Draw(Table(("experiment_id", "treatment", "control"), ("str", "int", "int"), tuple(rows)),
                {"broken": tuple(broken), "kinds": {"healthy": healthy, "gross": gross, "subtle": 1 - healthy - gross},
                 "generator": V + ":srm_history"})


def _binomial(rng: random.Random, n: int, p: float) -> int:
    """Normal approximation (n ≥ 4,000, so the error is negligible for SRM-sized deviations)."""
    return max(0, min(n, round(rng.gauss(n * p, (n * p * (1 - p)) ** 0.5))))
