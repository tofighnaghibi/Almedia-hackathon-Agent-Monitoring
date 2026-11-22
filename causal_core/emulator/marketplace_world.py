"""S1 world: a ratio metric split by a segment dimension, with a traffic-mix shift and a reporting delay.

Truth: per-segment conversion rates do not change; the share of traffic from a low-converting segment grows in the
current week, so the total rate falls (a mix effect). The newest day is only partly loaded (completeness 0.62), which
a naive day-over-day rule reads as a crash. The core never imports this module.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

GENERATOR_VERSION = "emulator/0.4:marketplace_world"
SOURCE = "emu-market"
CUT = datetime(2026, 11, 22, 6, 0, tzinfo=timezone.utc)
SEGMENTS = {"DE": 0.060, "FR": 0.050, "US": 0.045, "BR": 0.020}


def scope() -> dict:
    return {"environment_id": "emu-market-1", "environment_kind": "emulator", "generator_version": GENERATOR_VERSION}


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class MarketWorld:
    observations: tuple[dict, ...]
    dimension: dict
    window: dict
    truth: dict = field(default_factory=dict)
    daily_totals: tuple[tuple[str, float], ...] = ()


def build(seed: int = 11, visitors_per_day: int = 40_000, shift_share: float = 0.30, late_completeness: float = 0.62) -> MarketWorld:
    rng = random.Random(seed)
    start = CUT - timedelta(days=14, hours=6)
    base_share = {"DE": 0.35, "FR": 0.25, "US": 0.30, "BR": 0.10}
    cur_share = {"DE": 0.28, "FR": 0.20, "US": 0.22, "BR": shift_share}
    obs, totals = [], []
    for d in range(14):
        day = start + timedelta(days=d)
        share = base_share if d < 7 else cur_share
        late = d == 13
        num_t = den_t = 0.0
        for seg, rate in SEGMENTS.items():
            den = round(visitors_per_day * share[seg] * (late_completeness if late else 1.0))
            num = round(den * rate + rng.gauss(0, (den * rate * (1 - rate)) ** 0.5))
            num_t += num; den_t += den
            payload = {"shape": "aggregate_stats", "window": {"start": _iso(day), "end": _iso(day + timedelta(days=1))},
                       "n": den, "numerator": float(num), "denominator": float(den)}
            key = f"country:{seg}:{day.date()}"
            h = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            obs.append({"schema_version": "2.0", "observation_id": f"web_conversion:country:{seg}:{day.date()}",
                        "variable_id": "web_conversion", "payload": payload, "source_id": SOURCE, "record_key": key,
                        "revision": 1, "scope": scope(), "segment": [{"dimension_id": "country", "value": seg}],
                        "lineage": {"source_id": SOURCE, "origin_kind": "original",
                                    "lineage_root_id": f"{SOURCE}:{key}", "content_hash": h},
                        "clocks": {"observed_at": {"quality": "KNOWN", "value": _iso(day + timedelta(days=1))},
                                   "available_at": {"quality": "KNOWN", "value": _iso(day + timedelta(days=1, hours=4))}},
                        "dq": {"completeness": late_completeness if late else 1.0,
                               "maturity": "IMMATURE" if late else "MATURE"}})
        totals.append((str(day.date()), num_t / den_t))
    dimension = {"schema_version": "2.0", "dimension_id": "country", "scope": scope(),
                 "provenance": {"source_id": SOURCE, "method": "adapter_declared"},
                 "partition_policy": "exhaustive_declared", "values": list(SEGMENTS)}
    window = {"start": _iso(start + timedelta(days=7)), "end": _iso(start + timedelta(days=14))}
    return MarketWorld(tuple(obs), dimension, window,
                       {"segment_rates": SEGMENTS, "base_share": base_share, "current_share": cur_share,
                        "explanation": "mix", "immature_day": str((start + timedelta(days=13)).date()),
                        "generator": GENERATOR_VERSION}, tuple(totals))
