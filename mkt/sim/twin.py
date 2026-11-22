"""Phase 5 (+P1 look-alike scenarios): action gateway, interventions, counterfactual twin, evaluator-only oracle.

The oracle (`Truth`) is returned separately and is never given to the EmulatorPort; the reader only
ever sees the port (gate S6). World interventions change the simulated marketplace; observation
interventions change only what is logged (the twin's world equals the factual world).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
import polars as pl

from ..interventions import parse_selector
from ..engine_types import FailureCode, InterventionEvent, InterventionSpec
from .chain import ChainParams, simulate
from .chain import uniform
from .observe_chain import EmulatorPort, ObservationModel, SessionEmulatorPort, observe

EVENT_TYPES = {"order_created", "payment_approved", "handed_to_carrier", "delivered", "survey_sent", "review_submitted"}
ALLOWED = {
    "carrier.transit_multiplier": {"layer": "world", "operators": {"scale"}, "bounds": (0.5, 5.0), "selector_keys": {"region"}},
    "demand.multiplier": {"layer": "world", "operators": {"scale"}, "bounds": (0.2, 3.0), "selector_keys": set()},
    "checkout.failure_rate": {"layer": "world", "operators": {"set"}, "bounds": (0.0, 0.9), "selector_keys": set()},
    "storefront.payment_failure": {"layer": "operational", "operators": {"set"}, "bounds": (0.0, 1.0), "selector_keys": set()},
    "logging.drop_rate": {"layer": "observation", "operators": {"set"}, "bounds": (0.0, 0.9), "selector_keys": {"event_type"}},
}


def gateway(spec: InterventionSpec, regions: set[str], horizon: tuple[datetime, datetime]):
    """Validate against the allowlist; return (InterventionEvent, effect dict | None)."""
    def reject(code: FailureCode, why: str):
        return InterventionEvent(spec_id=spec.id, status="rejected", at=spec.window_start, failure_code=code.value, reason=why), None

    rule = ALLOWED.get(spec.target)
    if rule is None:
        return reject(FailureCode.UNSUPPORTED_REQUEST, f"target {spec.target!r} not on the allowlist {sorted(ALLOWED)}")
    if spec.operator not in rule["operators"]:
        return reject(FailureCode.UNSUPPORTED_REQUEST, f"operator {spec.operator!r} not allowed for {spec.target}")
    lo, hi = rule["bounds"]
    if not (isinstance(spec.value, (int, float)) and lo <= float(spec.value) <= hi):
        return reject(FailureCode.UNSUPPORTED_REQUEST, f"value {spec.value!r} outside safety bounds [{lo}, {hi}]")
    try:
        clauses = parse_selector(spec.scope)
    except ValueError as e:
        return reject(FailureCode.UNSUPPORTED_REQUEST, str(e))
    known = {"region": regions, "event_type": EVENT_TYPES}
    for key, _, vals in clauses:
        if key not in rule["selector_keys"]:
            return reject(FailureCode.UNSUPPORTED_REQUEST, f"selector key {key!r} not allowed for {spec.target}")
        if not set(vals) <= known[key]:
            return reject(FailureCode.UNSUPPORTED_REQUEST, f"unknown {key}(s) {sorted(set(vals) - known[key])}")
    if not (horizon[0] <= spec.window_start < spec.window_end <= horizon[1]):
        return reject(FailureCode.UNSUPPORTED_REQUEST, "window outside the simulation horizon")

    w0 = int(np.datetime64(spec.window_start, "us").astype(np.int64))
    w1 = int(np.datetime64(spec.window_end, "us").astype(np.int64))
    factor = float(spec.value)
    ok = InterventionEvent(spec_id=spec.id, status="executed", at=spec.window_start)

    if spec.target == "carrier.transit_multiplier":
        def transit(df: pl.DataFrame) -> np.ndarray:
            shipped_us = df["shipped_us"].to_numpy()                   # absolute time: chunk-invariant
            m = (shipped_us >= w0) & (shipped_us < w1)
            for key, op, vals in clauses:
                hit = np.isin(df["buyer_region"].to_numpy(), vals)
                m &= ~hit if op == "!=" else hit
            return np.where(m, factor, 1.0)
        return ok, {"kind": "transit", "fn": transit}
    if spec.target == "demand.multiplier":
        d0, d1 = spec.window_start.date(), spec.window_end.date()
        return ok, {"kind": "demand", "fn": lambda day: factor if d0 <= day < d1 else 1.0}
    if spec.target == "storefront.payment_failure":            # executed by the flag adapter in storefront windows
        variants = {0.0: "off", 0.1: "10%", 0.25: "25%", 0.5: "50%", 0.75: "75%", 1.0: "100%"}
        if factor not in variants:
            return reject(FailureCode.UNSUPPORTED_REQUEST, f"payment failure must be one of {sorted(variants)}")
        return ok, {"kind": "flag", "flag": "paymentFailure", "variant": variants[factor], "w0": w0, "w1": w1}
    if spec.target == "checkout.failure_rate":
        return ok, {"kind": "checkout_fail", "rate": factor, "w0": w0, "w1": w1}
    etypes = [v for _, _, vals in clauses for v in vals] or sorted(EVENT_TYPES)
    return ok, {"kind": "drop", "drops": [(e, spec.window_start, spec.window_end, factor) for e in etypes]}


@dataclass
class Truth:
    """Evaluator-only. Never passed to the port or the reader."""
    specs: list[InterventionSpec]
    causes: list[dict]                                   # [{target, layer, window_start, window_end, scope, value}]
    world_changed: bool                                  # factual world != twin world
    per_order: pl.DataFrame | None = None                # only for paired world interventions on fulfilment
    effect_late_share_affected: float = 0.0
    effect_late_share_region_weeks: dict[str, float] = field(default_factory=dict)
    sessions_factual: tuple | None = None                # (sessions, views) world frames, evaluator-only
    sessions_twin: tuple | None = None

    @property
    def spec(self) -> InterventionSpec:                  # backwards compatible (single intervention)
        return self.specs[0]


def run(params: ChainParams, start: date, end: date, seed: int, specs, om: ObservationModel = ObservationModel(), session_params=None):
    """specs: None, one InterventionSpec, or a list. Returns (events, factual, twin, port, truth).
    session_params: if given, the P1-2 session layer wraps the chain and the port serves funnel metrics;
    truth.sessions_factual / sessions_twin then hold the world-level sessions (evaluator-only)."""
    specs = [] if specs is None else ([specs] if isinstance(specs, InterventionSpec) else list(specs))
    horizon = (datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()))
    regions = set(params.buyer_regions)
    events, transit, demand, drops, cfail = [], None, None, [], None
    for sp in specs:
        ev, eff = gateway(sp, regions, horizon)
        events.append(ev)
        if eff is None:
            continue
        if eff["kind"] == "transit":
            transit = eff["fn"]
        elif eff["kind"] == "demand":
            demand = eff["fn"]
        elif eff["kind"] == "checkout_fail":
            cfail = eff
        else:
            drops += eff["drops"]
    reached = simulate(params, start, end, seed=seed, transit_multiplier=transit, demand_multiplier=demand)   # orders that reached checkout
    twin = simulate(params, start, end, seed=seed)
    failed = np.zeros(reached.height, bool)
    if cfail is not None:
        pu = reached["purchased_at"].cast(pl.Int64).to_numpy()
        failed = (pu >= cfail["w0"]) & (pu < cfail["w1"]) & (uniform(seed, "checkout:fail", reached["order_idx"].to_numpy()) < cfail["rate"])
    factual = reached.filter(pl.Series(~failed))
    observed = observe(factual, om, seed=seed, drops=drops)
    sess_f = sess_t = None
    if session_params is not None:
        from .sessions import build, observe_sessions
        sess_f = build(reached, session_params, params, start, end, seed, failed=failed, demand_multiplier=demand)
        sess_t = build(twin, session_params, params, start, end, seed) if specs else None   # twin only needed with interventions
        observed = pl.concat([observed, observe_sessions(*sess_f, seed=seed, web_delay_median_s=om.web_delay_median_s)],
                             how="diagonal_relaxed").sort("available_at")
        port = SessionEmulatorPort(observed, params, om, horizon[0], seed, session_params)
    else:
        port = EmulatorPort(observed, params, om, horizon[0], seed)
    executed = [sp for sp, ev in zip(specs, events) if ev.status == "executed"]
    if not executed:
        return (events[0] if len(events) == 1 else events), factual, twin, port, (
            Truth(specs=[], causes=[], world_changed=False, sessions_factual=sess_f, sessions_twin=sess_t) if sess_f else None)
    truth = Truth(specs=executed, world_changed=not factual.equals(twin),
                  causes=[{"target": sp.target, "layer": ALLOWED[sp.target]["layer"], "scope": sp.scope, "value": sp.value,
                           "window_start": sp.window_start.isoformat(), "window_end": sp.window_end.isoformat()} for sp in executed],
                  sessions_factual=sess_f, sessions_twin=sess_t)
    if transit is not None and factual.height == twin.height:
        late = lambda w: ((w["delivered_at"].dt.truncate("1d") - w["promised_at"]).dt.total_days() > 0)
        po = pl.DataFrame({"order_idx": factual["order_idx"], "region": factual["buyer_region"],
                           "week": factual["purchased_at"].dt.truncate("1w").dt.date().cast(pl.Utf8),
                           "affected": factual["delivered_at"] != twin["delivered_at"],
                           "late_fact": late(factual), "late_twin": late(twin),
                           "score_fact": factual["score"], "score_twin": twin["score"]})
        aff = po.filter(pl.col("affected"))
        scoped = [v for sp in executed if sp.target == "carrier.transit_multiplier" for _, _, vals in parse_selector(sp.scope) for v in vals]
        reg = po.filter(pl.col("region").is_in(scoped) & pl.col("late_fact").is_not_null() & pl.col("late_twin").is_not_null())
        weeks = reg.group_by("week").agg((pl.col("late_fact").mean() - pl.col("late_twin").mean()).alias("d")).sort("week")
        truth.per_order = po
        truth.effect_late_share_affected = float(aff["late_fact"].mean() - aff["late_twin"].mean()) if aff.height else 0.0
        truth.effect_late_share_region_weeks = dict(zip(weeks["week"].to_list(), weeks["d"].to_list()))
    return (events[0] if len(events) == 1 else events), factual, twin, port, truth
