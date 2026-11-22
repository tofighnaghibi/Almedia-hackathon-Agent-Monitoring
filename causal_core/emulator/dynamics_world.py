"""Phase 7 world: daily spend → visitors → trials, with a lag. Two mechanism FAMILIES, hidden from the core:

  linear:      visitors_t = 200 + 8·spend_t + e,                  e ~ N(0, 40)
  saturating:  visitors_t = 200 + 2400·(1 − exp(−spend_t/250)) + e (close to linear on the historical range,
                                                                     far below it at high spend)
  both:        trials_t   = 0.05·visitors_t + 0.3·trials_{t−1} + u, u ~ N(0, 5)
Spend is set exogenously (varied by a historical budget policy within [50, 150]), so fitted mechanisms are causal
in this world by construction. The harness alone computes the true counterfactual effect of an intervention.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np

GENERATOR_VERSION = "emulator/0.5:dynamics_world"
SOURCE = "emu-dynamics"
START = datetime(2026, 5, 1, tzinfo=timezone.utc)


def scope() -> dict:
    return {"environment_id": "emu-dynamics-1", "environment_kind": "emulator", "generator_version": GENERATOR_VERSION}


def visitors_mean(spend, family: str):
    spend = np.asarray(spend, dtype=float)
    return 200 + 8 * spend if family == "linear" else 200 + 2400 * (1 - np.exp(-spend / 250))


@dataclass(frozen=True)
class DynamicsWorld:
    family: str
    days: int
    series: dict            # variable → {datetime: value}
    observations: tuple
    truth: dict = field(default_factory=dict)


def _iso(t):
    return t.isoformat().replace("+00:00", "Z")


def build(seed: int = 1, family: str = "linear", days: int = 200) -> DynamicsWorld:
    rng = np.random.default_rng(seed)
    ts = [START + timedelta(days=i) for i in range(days)]
    spend = rng.uniform(50, 150, days)
    vis = visitors_mean(spend, family) + rng.normal(0, 40, days)
    trials = np.zeros(days)
    trials[0] = 0.05 * vis[0] / 0.7
    for t in range(1, days):
        trials[t] = 0.05 * vis[t] + 0.3 * trials[t - 1] + rng.normal(0, 5)
    series = {"spend": dict(zip(ts, spend.tolist())), "visitors": dict(zip(ts, vis.tolist())),
              "trials": dict(zip(ts, trials.tolist()))}
    obs = []
    for v, vals in series.items():
        points = [{"t": _iso(t), "value": round(x, 6)} for t, x in vals.items()]
        payload = {"shape": "time_series", "points": points}
        h = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        obs.append({"schema_version": "2.0", "observation_id": f"{v}:daily", "variable_id": v, "payload": payload,
                    "source_id": SOURCE, "record_key": f"{v}-daily", "revision": 1, "scope": scope(),
                    "lineage": {"source_id": SOURCE, "origin_kind": "original", "lineage_root_id": f"{SOURCE}:{v}",
                                "content_hash": h},
                    "clocks": {"observed_at": {"quality": "KNOWN", "value": _iso(ts[-1])},
                               "available_at": {"quality": "KNOWN", "value": _iso(ts[-1] + timedelta(hours=6))}}})
    return DynamicsWorld(family, days, series, tuple(obs),
                         {"family": family, "seed": seed, "last_trials": float(trials[-1]), "last_spend": float(spend[-1]),
                          "mean7_spend": float(spend[-7:].mean()), "generator": GENERATOR_VERSION})


def true_effect(world: DynamicsWorld, spend_value: float, horizon: int, baseline: str = "mean_7") -> dict:
    """Expected change in total trials over the horizon if spend is set to spend_value from tomorrow, versus the
    baseline policy the engine declares (default: spend held at its 7-day mean). Exact in expectation."""
    def total(spend):
        m_vis = float(visitors_mean([spend], world.family)[0])
        tr, s = world.truth["last_trials"], 0.0
        for _ in range(horizon):
            tr = 0.05 * m_vis + 0.3 * tr
            s += tr
        return s
    base = total(world.truth["mean7_spend"] if baseline == "mean_7" else world.truth["last_spend"])
    new = total(spend_value)
    return {"baseline_total": base, "intervened_total": new, "effect": new - base}
