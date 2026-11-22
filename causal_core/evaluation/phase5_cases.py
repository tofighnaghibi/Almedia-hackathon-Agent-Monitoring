"""Phase 5 harness: wires the emulator world → canonical adapter → coordinator, and scores results against truth.
Evaluation side only: the core never imports this module.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from adapters.outputs.sqlite_sinks import IdempotentSink
from adapters.sources.canonical_port import CanonicalEvidencePort
from causal_core import api
from causal_core.analytics.voi import QueryOption
from causal_core.orchestration.coordinator import TaskCoordinator
from causal_core.orchestration.planner import MonitorSpec, TaskContext
from causal_core.storage.ledger import Ledger
from emulator import experiment_world as W

CUT = "2026-11-22T06:00:00Z"


def task(tid: str, question: dict, trigger: str = "explicit_question", *, scope: dict | None = None, max_queries: int = 6,
         max_wall_ms: int = 10_000, incident_id: str | None = None):
    raw = {"schema_version": "2.0", "task_id": tid, "trigger": trigger, "source_scope": scope or W.scope(), "priority": 5,
           "data_cut": {"as_of": CUT}, "budget": {"max_wall_ms": max_wall_ms, "max_queries": max_queries},
           "created_at": CUT, "question": question}
    if incident_id:
        raw["incident_id"] = incident_id
    return api.parse_reasoning_task(raw)


def estimand(functional: str, scope: dict | None = None, target: dict | None = None) -> dict:
    s = scope or W.scope()
    return {"outcome": "trial_start_7d", "contrast": {"treatment": "variant", "control": "control"},
            "population": "new web visitors", "horizon": "7d", "functional": functional, "source_scope": s,
            "target_scope": target or s}


Q_ITT = {"question_kind": "ESTIMATE_EFFECT", "estimand": estimand("ITT"), "study_id": "checkout-01"}
Q_ATE = {"question_kind": "ESTIMATE_EFFECT", "estimand": estimand("ATE"), "study_id": "checkout-01"}
Q_STUDY = {"question_kind": "ASSESS_STUDY", "study_id": "checkout-01", "outcomes": ["trial_start_7d"]}
Q_MONITOR = {"question_kind": "DETECT_CHANGE", "variables": ["trial_conversion_daily"], "baseline_policy": "declared null"}
Q_PREDICT = {"question_kind": "PREDICT_OUTCOME", "target": {"variable_id": "trial_start_7d", "horizon": "30d"}}
Q_DESIGN = {"question_kind": "DESIGN_EVIDENCE", "competing_hypotheses": [
    {"statement": "the variant's price display causes the drop", "source": "price_display", "target": "trial_start_7d",
     "observable_implications": ["drop concentrated in variant traffic"]},
    {"statement": "a tracking change causes the drop", "source": "tracking_release", "target": "trial_start_7d",
     "observable_implications": ["drop appears in all arms on the release day"]}]}
Q_NO_STUDY = {"question_kind": "ESTIMATE_EFFECT", "estimand": estimand("ITT")}


def design_options() -> tuple[tuple[str, ...], tuple[QueryOption, ...]]:
    h = ("price_display", "tracking_release")
    pair = frozenset({frozenset(h)})
    return h, (QueryOption("split_drop_by_arm", pair, 1.0, frozenset({"r-arm"}), True, latency_ms=800, cost_units=1.0),
               QueryOption("release_log_lookup", pair, 0.8, frozenset({"r-rel"}), True, latency_ms=300),
               QueryOption("rerun_ab_with_holdout", pair, 1.0, frozenset(), False,
                           recommendation="re-run the price display as an A/B test with a tracking holdout"))


def harness(world: W.ExperimentWorld | None = None, *, studies: bool = True, monitors: bool = True,
            directory: Path | None = None, **port_kwargs):
    world = world or W.build()
    d = directory or Path(tempfile.mkdtemp(prefix="p5-"))
    ledger, sink = Ledger(d / "ledger.db"), IdempotentSink(d / "sink.db")
    port = CanonicalEvidencePort(world.observations, "emu-product-port", **port_kwargs)
    hyps, opts = design_options()
    ctx = TaskContext(studies={k: api.parse_study_design(v) for k, v in world.designs.items()} if studies else {},
                      monitors={"trial_conversion_daily": MonitorSpec(world.truth["series_null"]["mean"],
                                                                     world.truth["series_null"]["sd"])} if monitors else {},
                      hypotheses=hyps, query_options=opts)
    return TaskCoordinator(ledger, port, sink, context=ctx), sink, ledger, world


def truth_check(itt_result, monitor_result, world: W.ExperimentWorld) -> dict:
    """Read the hidden truth (here only) and score what the core said."""
    claim = itt_result.state.claims[0] if itt_result.state.claims else None
    lo, hi = claim.uncertainty.interval if claim else (None, None)
    mon = next(r for m, r in monitor_result.state.results if m == "savi_eprocess_v1")
    first = mon.values["first_crossing_index"]
    return {"itt_truth": world.truth["itt"], "itt_estimate": claim.estimate if claim else None,
            "itt_ci_covers_truth": bool(claim and lo <= world.truth["itt"] <= hi),
            "exposure_effect_truth": world.truth["exposure_effect"],
            "exposure_effect_claimed": any(c.estimand.functional != "ITT" for c in itt_result.state.claims),
            "drop_day_truth": world.truth["drop_day"], "alarm_point": first,
            "detection_delay_days": None if first is None else first - world.truth["drop_day"],
            "alarm_before_drop": first is not None and first < world.truth["drop_day"]}
