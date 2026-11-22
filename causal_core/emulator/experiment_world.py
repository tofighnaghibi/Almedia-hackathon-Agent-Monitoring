"""A product-experimentation world for the Phase 5/6 demos: one randomized study with one-sided noncompliance and
a daily conversion series with a level drop. Emits canonical wire dicts plus hidden truth.

The core never imports this module; adapters serve the visible part and the evaluation harness reads the truth.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

GENERATOR_VERSION = "emulator/0.4:experiment_world"
SOURCE = "emu-product"
CUT = datetime(2026, 11, 22, 6, 0, tzinfo=timezone.utc)


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def scope(environment_id: str = "emu-product-1") -> dict:
    return {"environment_id": environment_id, "environment_kind": "emulator", "generator_version": GENERATOR_VERSION}


def _obs(var: str, key: str, payload: dict, observed: datetime, available: datetime, env: str) -> dict:
    h = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return {"schema_version": "2.0", "observation_id": f"{var}:{key}", "variable_id": var, "payload": payload,
            "source_id": SOURCE, "record_key": key, "revision": 1, "scope": scope(env),
            "lineage": {"source_id": SOURCE, "origin_kind": "original", "lineage_root_id": f"{SOURCE}:{var}:{key}",
                        "content_hash": h},
            "clocks": {"observed_at": {"quality": "KNOWN", "value": _iso(observed)},
                       "available_at": {"quality": "KNOWN", "value": _iso(available)}}}


@dataclass(frozen=True)
class ExperimentWorld:
    observations: tuple[dict, ...]
    designs: dict
    truth: dict = field(default_factory=dict)


def build(seed: int = 7, n: int = 6000, base: float = 0.20, effect: float = 0.05, take_up: float = 0.7,
          treated_share: float = 0.5, days: int = 60, drop_day: int = 42, drop: float = -0.012, daily_sd: float = 0.006,
          env: str = "emu-product-1", late_days: int = 0, weekly: tuple[float, ...] | None = None) -> ExperimentWorld:
    """effect: conversion lift for users who actually see the variant. Assignment (ITT) effect = take_up × effect.
    treated_share ≠ 0.5 under a declared 50/50 design produces a sample ratio mismatch.
    late_days: the last `late_days` daily points become available only after the data cut.
    weekly: optional weekday pattern (Mon..Sun) in units of daily_sd, added to the daily series."""
    rng = random.Random(seed)
    units = []
    for i in range(n):
        arm = "variant" if rng.random() < treated_share else "control"
        exposed = 1 if arm == "variant" and rng.random() < take_up else 0
        p = base + effect * exposed
        units.append({"unit_id": f"u{i:05d}", "arm": arm, "outcome": 1.0 if rng.random() < p else 0.0, "exposure": exposed})
    study_end = CUT - timedelta(days=1)
    ao = _obs("trial_start_7d", "study-checkout-01", {"shape": "assignment_outcomes", "study_id": "checkout-01",
                                                      "arms": ["variant", "control"], "units": units},
              study_end, study_end + timedelta(hours=2), env)
    design = {"schema_version": "2.0", "design": "randomized", "study_id": "checkout-01", "scope": scope(env),
              "provenance": {"source_id": SOURCE, "method": "adapter_declared"}, "population": "new web visitors",
              "outcomes": ["trial_start_7d"], "randomization": "simple", "assignment_unit": "user", "outcome_unit": "user",
              "expected_allocation": {"variant": 0.5, "control": 0.5}, "compliance": "one_sided",
              "interference": "none_assumed"}
    start = CUT - timedelta(days=days)
    points, avail = [], []
    for d in range(days):
        t = start + timedelta(days=d)
        level = base + (drop if d >= drop_day else 0.0) + (weekly[t.weekday()] * daily_sd if weekly else 0.0)
        points.append({"t": _iso(t), "value": round(level + rng.gauss(0, daily_sd), 6)})
        avail.append(t + timedelta(hours=6))
    late_cutoff = len(points) - late_days
    vis = points[:late_cutoff]
    series = _obs("trial_conversion_daily", "daily", {"shape": "time_series", "points": vis},
                  start + timedelta(days=len(vis) - 1), avail[len(vis) - 1], env)
    return ExperimentWorld((ao, series), {"checkout-01": design},
                           {"itt": take_up * effect, "exposure_effect": effect, "take_up": take_up,
                            "srm": treated_share != 0.5, "series_null": {"mean": base, "sd": daily_sd},
                            "drop": drop, "drop_day": drop_day, "weekly": weekly, "series_start": _iso(start),
                            "generator": GENERATOR_VERSION})
