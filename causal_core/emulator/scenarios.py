"""Scenario emulator — Phase 0 seed.

Each generator is a small, seeded structural causal model. It returns a
``Draw``: the *visible* table an input adapter could legitimately see, and a
separate *truth* record (mechanism parameters, true effects). The split is the
whole point. ``causal_core`` must never import this package (G01, I16), and
fixtures only ever receive ``Draw.visible``. Truth goes to ``evaluation/truth``.

Stdlib only (``random.Random``) so Phase 0 runs with zero installs. Later
phases add worlds (marketplace, study, sensor) behind the same Draw contract.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

GENERATOR_VERSION = "emulator/0.1"


@dataclass(frozen=True)
class Table:
    columns: tuple[str, ...]
    types: tuple[str, ...]  # "str" | "int" | "float"
    rows: tuple[tuple, ...]


@dataclass(frozen=True)
class Draw:
    visible: Table
    truth: dict = field(default_factory=dict)


def _r(x: float) -> float:
    # Round once, at the emulator boundary, so the stored CSV *is* the data.
    return round(x, 6)


def cluster_randomized_trial(
    seed: int,
    n_clusters: int = 20,
    cluster_size: int = 50,
    icc: float = 0.10,
    effect: float = 0.15,
) -> Draw:
    """Clusters are randomized; outcomes are measured on individuals.

    y = effect * treated(cluster) + u_cluster + e_individual, with
    Var(u) = icc and Var(e) = 1 - icc, so total variance is 1 and the
    intra-cluster correlation is exactly ``icc``.
    """
    rng = random.Random(seed)
    clusters = [f"c{i:02d}" for i in range(n_clusters)]
    treated = set(rng.sample(clusters, n_clusters // 2))
    rows = []
    for c in clusters:
        u = rng.gauss(0.0, icc ** 0.5)
        arm = "treatment" if c in treated else "control"
        for _ in range(cluster_size):
            e = rng.gauss(0.0, (1.0 - icc) ** 0.5)
            rows.append((c, arm, _r(effect * (arm == "treatment") + u + e)))
    return Draw(
        visible=Table(("cluster", "arm", "y"), ("str", "str", "float"), tuple(rows)),
        truth={
            "generator": f"{GENERATOR_VERSION}:cluster_randomized_trial",
            "seed": seed,
            "params": {"n_clusters": n_clusters, "cluster_size": cluster_size, "icc": icc},
            "true_values": {"average_treatment_effect": effect,
                            "design_effect": 1 + (cluster_size - 1) * icc},
        },
    )


def latent_confounded_pair(
    seed: int, n: int = 1500, a: float = 0.8, beta: float = 0.5, gamma: float = 1.0
) -> Draw:
    """U -> X, U -> Y, X -> Y. Only X and Y are visible.

    OLS of Y on X converges to beta + gamma*a / (a^2 + 1), not beta.
    """
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        u = rng.gauss(0.0, 1.0)
        x = a * u + rng.gauss(0.0, 1.0)
        y = beta * x + gamma * u + rng.gauss(0.0, 1.0)
        rows.append((f"r{i:05d}", _r(x), _r(y)))
    return Draw(
        visible=Table(("row", "x", "y"), ("str", "float", "float"), tuple(rows)),
        truth={
            "generator": f"{GENERATOR_VERSION}:latent_confounded_pair",
            "seed": seed,
            "params": {"a": a, "gamma": gamma, "latent": "U ~ N(0,1)"},
            "true_values": {"causal_effect_x_on_y": beta,
                            "ols_probability_limit": beta + gamma * a / (a * a + 1)},
        },
    )


def one_sided_noncompliance(
    seed: int,
    n: int = 2000,
    complier_share: float = 0.6,
    effect_on_exposed: float = 1.0,
    never_taker_shift: float = -0.5,
) -> Draw:
    """Z is randomized; only compliers take up exposure D when Z = 1.

    Never-takers have a lower baseline, so comparing D=1 vs D=0 ("as treated")
    is confounded by selection into exposure. Compliance type is hidden.
    """
    rng = random.Random(seed)
    rows = []
    n_compliers = 0
    for i in range(n):
        z = 1 if rng.random() < 0.5 else 0
        complier = rng.random() < complier_share
        n_compliers += complier
        d = 1 if (z == 1 and complier) else 0
        y = effect_on_exposed * d + (0.0 if complier else never_taker_shift) + rng.gauss(0.0, 1.0)
        rows.append((f"u{i:05d}", z, d, _r(y)))
    return Draw(
        visible=Table(("unit", "z", "d", "y"), ("str", "int", "int", "float"), tuple(rows)),
        truth={
            "generator": f"{GENERATOR_VERSION}:one_sided_noncompliance",
            "seed": seed,
            "params": {"complier_share": complier_share, "never_taker_shift": never_taker_shift,
                       "realised_complier_share": n_compliers / n},
            "true_values": {"itt_effect_of_assignment": complier_share * effect_on_exposed,
                            "complier_average_causal_effect": effect_on_exposed},
        },
    )


def parallel_trends_panel(
    seed: int,
    n_treated: int = 8,
    n_control: int = 8,
    pre_periods: int = 3,
    att: float = 1.5,
    trend: float = 0.5,
    noise_sd: float = 0.3,
) -> Draw:
    """Unit fixed effects + common linear trend + ATT from period 0 onward.

    Parallel trends hold *by construction* here; a later phase adds the
    adversarial twin where the pre-trend looks fine but the assumption fails.
    """
    rng = random.Random(seed)
    rows = []
    for group, count in (("treated", n_treated), ("control", n_control)):
        for k in range(count):
            unit = f"{group[0]}{k:02d}"
            alpha = rng.gauss(10.0 if group == "treated" else 9.0, 0.5)
            for t in range(-pre_periods, 1):
                treated_now = group == "treated" and t >= 0
                y = alpha + trend * t + att * treated_now + rng.gauss(0.0, noise_sd)
                rows.append((unit, group, t, _r(y)))
    return Draw(
        visible=Table(("unit", "group", "period", "y"), ("str", "str", "int", "float"), tuple(rows)),
        truth={
            "generator": f"{GENERATOR_VERSION}:parallel_trends_panel",
            "seed": seed,
            "params": {"trend": trend, "noise_sd": noise_sd, "parallel_trends": True},
            "true_values": {"att": att},
        },
    )
