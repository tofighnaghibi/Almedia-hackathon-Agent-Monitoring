"""Cold-chain world: one causal system seen through four variable kinds.

    door_open (event) ──lag ~10 min──▶ freezer_temp_c (state) ──▶ compressor_mode (categorical)
                                             │
                                  excursion minutes above −18 °C
                                             ▼
                              on_time_dispatch_rate (metric, daily ratio)

Emits what an input adapter would hand the core: schema v2.0 *wire dicts*
(plain JSON, no causal_core import), a v1.x legacy rendering of the same
facts, and a separate truth record for the evaluation harness only.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

GENERATOR_VERSION = "emulator/0.2:coldchain"
SOURCE = "emu-coldchain"
START = datetime(2026, 11, 18, tzinfo=timezone.utc)
REPORTING_DELAY = timedelta(hours=6)


@dataclass(frozen=True)
class World:
    definitions: dict[str, dict]
    observations: list[dict]
    legacy_records: list[str]
    truth: dict


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def _scope(data_cut: datetime) -> dict:
    return {"environment_id": "emu-coldchain-1", "environment_kind": "emulator",
            "generator_version": GENERATOR_VERSION, "data_cut": _iso(data_cut)}


_PROVENANCE = {"source_id": SOURCE, "method": "adapter_declared"}
_DAILY_SITE7 = {"population_coverage": {"population_id": "orders:site_7", "coverage": "full"},
                "time_semantics": {"grain": "1d", "window": "tumbling", "timezone": "UTC"}}


def definitions(data_cut: datetime) -> dict[str, dict]:
    base = {"schema_version": "2.0", "scope": _scope(data_cut), "provenance": _PROVENANCE}
    return {
        "orders": {**base, "kind": "metric", "variable_id": "orders", "unit": "orders",
                   "value_schema": {"type": "numeric", "dtype": "count", "minimum": 0},
                   "aggregation_kind": "sum", **_DAILY_SITE7},
        "dispatched_on_time": {**base, "kind": "metric", "variable_id": "dispatched_on_time", "unit": "orders",
                               "value_schema": {"type": "numeric", "dtype": "count", "minimum": 0},
                               "aggregation_kind": "sum", **_DAILY_SITE7},
        "on_time_dispatch_rate": {
            **base, "kind": "metric", "variable_id": "on_time_dispatch_rate", "unit": "1",
            "value_schema": {"type": "numeric", "dtype": "proportion", "minimum": 0, "maximum": 1},
            "aggregation_kind": "ratio",
            "formula_ast": {"node": "binop", "op": "div",
                            "left": {"node": "ref", "variable_id": "dispatched_on_time"},
                            "right": {"node": "ref", "variable_id": "orders"}},
            "population_coverage": {"population_id": "orders:site_7", "coverage": "full"},
            "time_semantics": {"grain": "1d", "window": "tumbling", "timezone": "UTC"},
            "identity_tolerance": 1e-4},
        "freezer_temp_c": {**base, "kind": "state", "variable_id": "freezer_temp_c", "unit": "degC",
                           "value_schema": {"type": "numeric", "dtype": "real", "minimum": -40, "maximum": 20},
                           "sampling_cadence_s": 300},
        "door_open": {**base, "kind": "event", "variable_id": "door_open", "occurrence_time": "event_time",
                      "value_schema": {"type": "event", "fields": {"door_id": "category", "duration_s": "real"},
                                       "identity_fields": ["door_id"]}},
        "compressor_mode": {**base, "kind": "categorical", "variable_id": "compressor_mode",
                            "value_schema": {"type": "categorical", "categories": ["off", "eco", "boost"],
                                             "exhaustive": True}},
    }


def _obs(var: str, key: str, payload: dict, observed: datetime, available: datetime, data_cut: datetime) -> dict:
    return {"schema_version": "2.0", "observation_id": f"{var}:{key}", "variable_id": var, "payload": payload,
            "source_id": SOURCE, "record_key": key, "revision": 1,
            "lineage": {"source_id": SOURCE, "origin_kind": "original", "lineage_root_id": f"{SOURCE}:{var}:{key}",
                        "content_hash": _sha(payload)},
            "scope": _scope(data_cut),
            "clocks": {"observed_at": {"quality": "KNOWN", "value": _iso(observed)},
                       "available_at": {"quality": "KNOWN", "value": _iso(available)},
                       "as_of": _iso(data_cut)},
            "dq": {"completeness": 1.0, "maturity": "MATURE"}}


def emit(seed: int = 20261121, days: int = 2, heat_per_open_min: float = 0.15,
         lag_min: int = 10, excursion_threshold: float = -18.0, dispatch_penalty: float = 0.004,
         drift_from_day: int | None = None, cancel_share: float = 0.04) -> World:
    """drift_from_day: from that day on the source silently drops cancelled orders from the reported
    rate's denominator (an undocumented filter). The declared definition does not change."""
    rng = random.Random(seed)
    data_cut = START + timedelta(days=days, hours=12)
    defs = definitions(data_cut)
    observations, legacy, daily = [], [], []
    temp = -21.0
    for day in range(days):
        day_start = START + timedelta(days=day)
        # door openings: times and durations
        events = []
        for k in range(rng.randint(10, 16)):
            t = day_start + timedelta(minutes=rng.randint(0, 1439))
            events.append((t, f"d{rng.randint(1, 3)}", round(rng.uniform(20, 240), 1), f"{day}-{k}"))
        events.sort()
        open_minutes = {}
        for t, _door, dur, _id in events:
            m = int((t - day_start).total_seconds() // 60) + lag_min
            open_minutes[m] = open_minutes.get(m, 0.0) + dur / 60.0
        # temperature, 1-minute physics, 5-minute samples; compressor mode every 3 h
        samples, modes, excursion = [], [], 0
        for minute in range(1440):
            temp += heat_per_open_min * open_minutes.get(minute, 0.0) * 6 - 0.05 * (temp + 21.0)
            temp += rng.gauss(0, 0.03)
            excursion += temp > excursion_threshold
            now = day_start + timedelta(minutes=minute)
            if minute % 5 == 0:
                samples.append({"t": _iso(now), "state": round(temp, 3)})
            if minute % 180 == 0:
                modes.append((now, "boost" if temp > excursion_threshold - 1 else "eco"))
        end = day_start + timedelta(days=1)
        observations.append(_obs("freezer_temp_c", f"day{day}", {"shape": "state_trajectory", "samples": samples},
                                 end, end + timedelta(minutes=5), data_cut))
        observations.append(_obs("door_open", f"day{day}", {"shape": "event_batch", "events": [
            {"event_id": f"door-{eid}", "occurred_at": {"quality": "KNOWN", "value": _iso(t)},
             "attributes": {"door_id": door, "duration_s": dur}} for t, door, dur, eid in events]},
            end, end + timedelta(minutes=5), data_cut))
        for t, mode in modes:
            observations.append(_obs("compressor_mode", _iso(t), {"shape": "categorical", "category": mode},
                                     t, t + timedelta(minutes=1), data_cut))
        orders = rng.randint(380, 460)
        rate = max(0.0, 0.97 - dispatch_penalty * excursion)
        on_time = sum(rng.random() < rate for _ in range(orders))
        window = {"start": _iso(day_start), "end": _iso(end)}
        drifted = drift_from_day is not None and day >= drift_from_day
        reported_den = orders - round(orders * cancel_share) if drifted else orders
        for var, payload in (("orders", {"n": orders, "total": float(orders)}),
                             ("dispatched_on_time", {"n": orders, "total": float(on_time)}),
                             ("on_time_dispatch_rate", {"n": reported_den, "numerator": on_time,
                                                        "denominator": reported_den})):
            observations.append(_obs(var, f"day{day}", {"shape": "aggregate_stats", "window": window, **payload},
                                     end, end + REPORTING_DELAY, data_cut))
        daily.append({"day": day, "excursion_minutes": excursion, "door_events": len(events), "drifted": drifted})
        legacy.append(json.dumps({"schema_version": "1.1", "kind": "Observation", "metric_id": "on_time_dispatch_rate",
                                  "window": window, "stats": {"numerator": on_time, "denominator": orders, "n": orders},
                                  "event_time": _iso(end - timedelta(seconds=1)), "as_of": _iso(data_cut),
                                  "revision_id": 1}, separators=(",", ":")))
    legacy.append(json.dumps({"schema_version": "1.0", "kind": "MetricDefinition", "metric_id": "on_time_dispatch_rate",
                              "unit": "orders/order", "aggregation_kind": "ratio", "time_grain": "1d",
                              "population": "orders:site_7", "formula": "div(dispatched_on_time, orders)"}))
    legacy.append(json.dumps({"schema_version": "1.1", "kind": "GraphEdge", "edge_kind": "HYPOTHESIZED_CAUSAL",
                              "source": "door_open", "target": "freezer_temp_c",
                              "min_lag_seconds": 300, "max_lag_seconds": 1200}))
    legacy.append(json.dumps({"schema_version": "1.2", "kind": "ExperimentRecord", "experiment_id": "door_alarm_ab",
                              "randomization_unit": "site", "exposure_unit": "order", "srm_p_value": 0.41}))
    return World(defs, observations, legacy, truth={
        "generator": GENERATOR_VERSION, "seed": seed,
        "mechanisms": {"door_open->freezer_temp_c": {"lag_min": lag_min, "heat_per_open_min": heat_per_open_min},
                       "freezer_temp_c->compressor_mode": {"boost_above_c": excursion_threshold - 1},
                       "excursion_minutes->on_time_dispatch_rate": {"penalty_per_minute": dispatch_penalty}},
        "drift": {"from_day": drift_from_day, "cancel_share": cancel_share,
                  "mechanism": "reported rate denominator excludes cancelled orders"},
        "daily": daily})


def declared_structure(data_cut: datetime) -> dict:
    """What the source *declares* about structure: domain knowledge, not hidden truth (no coefficients)."""
    scope = _scope(data_cut)

    def node(nid, kind, layer="DOMAIN"):
        return {"schema_version": "2.0", "node_id": nid, "kind": kind, "layer": layer}

    def edge(eid, kind, src, dst, layer="DOMAIN", lag=None, **extra):
        e = {"schema_version": "2.0", "edge_id": eid, "kind": kind, "source": src, "target": dst, "layer": layer,
             "status": "ACTIVE", "scope": scope,
             "lineage": {"source_id": SOURCE, "origin_kind": "original", "lineage_root_id": f"decl:{eid}",
                         "content_hash": _sha([eid, kind, src, dst])}, **extra}
        if lag:
            e["lag"] = {"min_s": lag[0], "max_s": lag[1]}
        return e

    return {
        "nodes": [node("door_alarm_assigned", "INTERVENTION"), node("door_alarm_active", "INTERVENTION"),
                  node("dispatch_reporting_lag", "OBSERVATION_MECHANISM", "OBSERVATION")],
        "edges": [
            edge("door-to-temp", "TEMPORAL_LAG", "door_open", "freezer_temp_c", lag=(300, 1200)),
            edge("temp-to-mode", "TEMPORAL_LAG", "freezer_temp_c", "compressor_mode", lag=(0, 300)),
            edge("mode-to-temp", "TEMPORAL_FEEDBACK", "compressor_mode", "freezer_temp_c", lag=(60, 900)),
            edge("temp-to-dispatch", "CAUSAL_HYPOTHESIS", "freezer_temp_c", "on_time_dispatch_rate"),
            edge("alarm-to-temp", "CAUSAL_HYPOTHESIS", "door_alarm_active", "freezer_temp_c"),
            edge("reporting-lag", "OBSERVATION_EFFECT", "dispatch_reporting_lag", "on_time_dispatch_rate",
                 layer="OBSERVATION"),
        ]}
