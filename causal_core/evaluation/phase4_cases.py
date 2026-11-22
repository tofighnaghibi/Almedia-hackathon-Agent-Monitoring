"""Phase 4 independent statistics: production kernels vs the Phase 0 oracle, and the adversarial suite
scored against emulator truth. Evaluation side only: the core never imports this module.

Each adversarial scenario's EXPECTED behaviour is written here, before any result is seen, so the
score can't be fitted to the output.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np

from causal_core import api
from causal_core.analytics import identification as ia
from causal_core.analytics.decomposition import Stratum, evaluate_kitagawa
from causal_core.analytics.descriptive import evaluate_association
from causal_core.analytics.experiments import evaluate_randomized
from causal_core.analytics.quasi_experimental import evaluate_did
from causal_core.contracts.graph import GraphEdge, GraphNode, Validity
from causal_core.graph.store import GraphDelta, GraphStore, NodeRecord
from causal_core.graph.views import CausalEvidenceView
from causal_core.storage.eprocess_state import consume, wealth_path
from emulator import adversarial
from evaluation import fixture_io as io

ENV = {"environment_id": "e", "environment_kind": "staging"}
_ENVELOPE = {"schema_version": "2.0", "observation_id": "o", "variable_id": "y", "source_id": "x", "record_key": "k",
             "revision": 1, "scope": ENV,
             "lineage": {"source_id": "x", "origin_kind": "original", "lineage_root_id": "r", "content_hash": "a" * 64}}


def rct_design(randomization: str = "simple", interference: str = "none_assumed", alloc=None):
    d = {"schema_version": "2.0", "design": "randomized", "study_id": "s", "scope": ENV,
         "provenance": {"source_id": "x", "method": "adapter_declared"}, "population": "p", "outcomes": ["y"],
         "randomization": randomization, "assignment_unit": "cluster" if randomization == "cluster" else "unit",
         "outcome_unit": "unit", "expected_allocation": alloc or {"treatment": 0.5, "control": 0.5}, "interference": interference}
    if randomization == "cluster":
        d["cluster_field"] = "cluster"
    return api.parse_study_design(d)


def did_design():
    return api.parse_study_design({"schema_version": "2.0", "design": "quasi_experimental", "study_id": "s", "scope": ENV,
                                   "provenance": {"source_id": "x", "method": "adapter_declared"}, "population": "p",
                                   "outcomes": ["y"], "strategy": "difference_in_differences", "unit": "unit",
                                   "treated_group": "treated", "intervention_period": 0})


def assignment(units):
    return api.parse_observation({**_ENVELOPE, "payload": {"shape": "assignment_outcomes", "study_id": "s",
                                                           "arms": ["treatment", "control"], "units": units}}).payload


def panel(cells):
    return api.parse_observation({**_ENVELOPE, "payload": {"shape": "panel", "cells": cells}}).payload


def estimand(functional: str, population: str = "p", source="e", target="e", source_kind="staging", target_kind="staging"):
    def scope(env, kind):
        d = {"environment_id": env, "environment_kind": kind}
        return {**d, "generator_version": "emulator/0.3"} if kind == "emulator" else d
    return api.parse_reasoning_task({
        "schema_version": "2.0", "task_id": "t", "trigger": "explicit_question", "source_scope": ENV, "priority": 5,
        "data_cut": {"as_of": "2026-11-20T00:00:00Z"}, "budget": {"max_wall_ms": 1000}, "created_at": "2026-11-20T00:00:00Z",
        "question": {"question_kind": "ESTIMATE_EFFECT", "estimand": {
            "outcome": "y", "contrast": {"treatment": "treatment", "control": "control"}, "population": population,
            "horizon": "14d", "functional": functional, "source_scope": scope(source, source_kind),
            "target_scope": scope(target, target_kind)}}}).question.estimand


# ── A. differential vs Phase 0 oracle ─────────────────────────────────────


def differential() -> list[dict]:
    out = []

    def row(fixture, quantity, prod, oracle):
        out.append({"fixture": fixture, "quantity": quantity, "production": prod, "oracle": oracle,
                    "abs_diff": abs(prod - oracle)})

    fx = json.loads((io.FIXTURE_DIR / "02_simpson_kitagawa.json").read_text())
    k = evaluate_kitagawa([Stratum(s["segment"], s["num0"], s["den0"], s["num1"], s["den1"]) for s in fx["input"]["segments"]])
    row("02", "mix_component", k.values["mix_component"], fx["expected"]["main"]["mix_component"])
    row("02", "rate_component", k.values["rate_component"], fx["expected"]["main"]["rate_component"])
    fx = json.loads((io.FIXTURE_DIR / "04_valid_rct.json").read_text())
    a, c = fx["input"]["assigned"], fx["input"]["converted"]
    units = [{"unit_id": f"{arm}{i}", "arm": arm, "outcome": 1.0 if i < c[arm] else 0.0}
             for arm in ("treatment", "control") for i in range(a[arm])]
    r = evaluate_randomized(rct_design(), assignment(units), "treatment", "control")
    row("04", "itt", r.values["estimate"], fx["expected"]["itt"]["estimate"])
    row("04", "itt_se", r.values["se"], fx["expected"]["itt"]["se"])
    row("04", "srm_p", r.values["srm_p_value"], fx["expected"]["srm_at_assignment_unit"]["p_value"])
    fx, rows = io.load_fixture("05_cluster_rct_iid_se")
    r = evaluate_randomized(rct_design("cluster"), assignment([{"unit_id": f"u{i}", "arm": x["arm"], "outcome": x["y"],
                                                              "cluster_id": x["cluster"]} for i, x in enumerate(rows)]),
                            "treatment", "control")
    for q in ("estimate", "se", "p_value"):
        row("05", f"cluster_{q}", r.values[q], fx["expected"]["cluster_analysis"][q])
    fx, rows = io.load_fixture("06_conditional_did")
    r = evaluate_did(did_design(), panel([{"unit": x["unit"], "period": x["period"], "value": x["y"]} for x in rows]),
                     {x["unit"]: x["group"] for x in rows}, "treated", "control")
    row("06", "att", r.values["estimate"], fx["expected"]["att"]["estimate"])
    row("06", "att_se", r.values["se"], fx["expected"]["att"]["se"])
    row("06", "pretrend_p", r.values["pretrend"]["p_value"], fx["expected"]["pretrend_slope_difference"]["p_value"])
    fx, rows = io.load_fixture("07_unidentifiable_confounder")
    r = evaluate_association([x["x"] for x in rows], [x["y"] for x in rows], "independent")
    row("07", "slope", r.values["slope"], fx["expected"]["association"]["slope"])
    row("07", "se_slope", r.values["se_slope"], fx["expected"]["association"]["se_slope"])
    fx = json.loads((io.FIXTURE_DIR / "08_revised_bucket_eprocess.json").read_text())
    consumed, lam = {}, fx["input"]["lambda"]
    for rec in fx["input"]["records_in_arrival_order"]:
        consumed, _ = consume(consumed, rec)
    row("08", "log_wealth", wealth_path(consumed, lambda z: lam * z - lam * lam / 2)[0], fx["expected"]["log_wealth"])
    fx, rows = io.load_fixture("11_noncompliance_itt_vs_exposure")
    u = [{"unit_id": x["unit"], "arm": "treatment" if x["z"] else "control", "outcome": x["y"], "exposure": x["d"]} for x in rows]
    r = evaluate_randomized(rct_design(), assignment(u), "treatment", "control", "CACE")
    row("11", "itt", r.values["itt"], fx["expected"]["itt"]["estimate"])
    row("11", "cace", r.values["estimate"], fx["expected"]["wald_iv_cace"]["estimate"])
    row("11", "cace_se", r.values["se"], fx["expected"]["wald_iv_cace"]["se"])
    return out


# ── C. adversarial suite scored against truth ─────────────────────────────


def _confounded_view():
    store = GraphStore()
    node = lambda n: NodeRecord(GraphNode(schema_version="2.0", node_id=n, kind="VARIABLE", variable_id=n), None, Validity())
    lat = GraphEdge.model_validate_json(json.dumps({"schema_version": "2.0", "edge_id": "u", "kind": "LATENT_CONFOUNDING",
                                                    "source": "x", "target": "y", "status": "ACTIVE", "scope": ENV,
                                                    "lineage": {"source_id": "x", "origin_kind": "original",
                                                                "lineage_root_id": "decl", "content_hash": "a" * 64}}))
    store.apply(GraphDelta(1, "r", "f" * 64, "s", "r1", datetime(2026, 1, 1, tzinfo=timezone.utc), (node("x"), node("y")), (lat,)))
    return CausalEvidenceView(store.pin())


def adversarial_suite() -> list[dict]:
    results = []

    def record(name, expected, observed, truth, ci=None, met=False):
        covers = None if ci is None or truth is None else bool(ci[0] <= truth <= ci[1])
        results.append({"scenario": name, "expected": expected, "observed": observed, "truth": truth,
                        "ci": ci, "ci_covers_truth": covers, "expectation_met": bool(met)})

    d = adversarial.pretrend_ok_but_violated()
    rows = [dict(zip(d.visible.columns, r)) for r in d.visible.rows]
    r = evaluate_did(did_design(), panel([{"unit": x["unit"], "period": x["period"], "value": x["y"]} for x in rows]),
                     {x["unit"]: x["group"] for x in rows}, "treated", "control")
    a = ia.assess(estimand("ATT"), "quasi_experimental", r, evidence_ids=("ev",))
    record("pretrend_ok_but_violated",
           "pre-trend diagnostic passes, status stays CONDITIONAL (never certified), parallel trends listed untestable",
           {"pretrend": r.values["pretrend"]["diagnostic"], "status": a.status, "estimate": round(r.values["estimate"], 3),
            "trend_sensitivity_fragile": r.values["sensitivity"]["fragile"]},
           d.truth["true_values"]["att"], r.values["ci"],
           r.values["pretrend"]["diagnostic"] == "NO_DETECTABLE_DIVERGENCE" and a.status == "CONDITIONAL_IDENTIFICATION"
           and any("parallel trends" in x for x in a.assumptions_untestable))

    d = adversarial.differential_attrition()
    units = [{"unit_id": u, "arm": arm, "outcome": y} for u, arm, y in d.visible.rows]
    r = evaluate_randomized(rct_design(), assignment(units), "treatment", "control")
    a = ia.assess(estimand("ITT"), "randomized", r, evidence_ids=("ev",))
    record("differential_attrition", "DIFFERENTIAL_ATTRITION flagged and status PARTIAL_IDENTIFICATION",
           {"status": a.status, "limitations": [l.split(":")[0] for l in r.limitations], "estimate": round(r.values["estimate"], 3)},
           d.truth["true_values"]["itt"], r.values["ci"],
           a.status == "PARTIAL_IDENTIFICATION" and any(l.startswith("DIFFERENTIAL_ATTRITION") for l in r.limitations))

    d = adversarial.spillover()
    units = [{"unit_id": u, "arm": arm, "outcome": y} for u, arm, y in d.visible.rows]
    r = evaluate_randomized(rct_design(interference="unknown"), assignment(units), "treatment", "control")
    a = ia.assess(estimand("ITT"), "randomized", r, evidence_ids=("ev",))
    record("spillover", "cannot be detected from these data; SUTVA must be listed as untestable",
           {"status": a.status, "estimate": round(r.values["estimate"], 3)}, d.truth["true_values"]["direct_effect"], r.values["ci"],
           any("SUTVA" in x for x in a.assumptions_untestable))

    d = adversarial.skewed_clusters()
    units = [{"unit_id": u, "arm": arm, "outcome": y, "cluster_id": c} for u, arm, c, y in d.visible.rows]
    r6 = evaluate_randomized(rct_design("cluster"), assignment(units), "treatment", "control")
    few = [x for x in units if x["cluster_id"][-1] in "012"]
    r3 = evaluate_randomized(rct_design("cluster"), assignment(few), "treatment", "control")
    record("skewed_clusters", "6/arm unequal → limitations (few + unequal); 3/arm → UNSUPPORTED_CLUSTER_INFERENCE",
           {"six_per_arm": r6.status, "limitations": len(r6.limitations), "three_per_arm": r3.refusal_code},
           d.truth["true_values"]["itt"], r6.values.get("ci"),
           r6.status == "OK_WITH_LIMITATIONS" and len(r6.limitations) >= 2 and r3.refusal_code == "UNSUPPORTED_CLUSTER_INFERENCE")

    rct, did = estimand("ATE", population="all sites"), estimand("ATT", population="self-selected sites")
    cmp = ia.compare_estimands(rct, did)
    record("ate_vs_att", "INCOMPARABLE: no blanket RCT override across estimands (C02, I09)",
           {"comparable": cmp.comparable, "differences": list(cmp.differences)}, None, None,
           not cmp.comparable and any("population" in x for x in cmp.differences))

    fx, rows = io.load_fixture("07_unidentifiable_confounder")
    r = evaluate_association([x["x"] for x in rows], [x["y"] for x in rows], "independent")
    a = ia.assess(estimand("ATE"), "observational", r, evidence_ids=("ev",), view=_confounded_view(),
                  treatment_node="x", outcome_node="y")
    obs_like = r.__class__(**{**r.__dict__, "claim_kind": "CAUSAL_EFFECT_CONDITIONAL"})   # even if a method claimed more
    a2 = ia.assess(estimand("ATE"), "observational", obs_like, evidence_ids=("ev",), view=_confounded_view(),
                   treatment_node="x", outcome_node="y", adjustment_set=("z",))
    truth = json.loads((io.TRUTH_DIR / "07_unidentifiable_confounder.json").read_text())["true_values"]["causal_effect_x_on_y"]
    record("unmeasured_confounding", "association reported; effect UNIDENTIFIED even with an unrelated adjustment set",
           {"status": a.status, "status_with_adjustment": a2.status, "association": round(r.values["slope"], 3)}, truth, r.values["ci"],
           a.status == "ASSOCIATION_ONLY" and a2.status == "UNIDENTIFIED")
    return results


# ── D. user decisions 2026-11-22: SRM warning threshold learned from simulated history ─────────────


def srm_calibration_study(seed: int = 45) -> dict:
    """Learn the SRM warning threshold on a simulated history, then score it against the emulator's truth.

    p-values come from the Phase 0 stdlib oracle (independent of production); the threshold comes from the
    production `calibrate_srm_warning`; whether each warning is right is read from truth, here only.
    """
    from causal_core.analytics.experiments import SRM_BLOCK_ALPHA, calibrate_srm_warning
    from emulator.adversarial import srm_history
    from evaluation import oracle

    def p_values(draw):
        return {r[0]: oracle.srm_two_arm({"t": r[1], "c": r[2]}, {"t": 0.5, "c": 0.5})["p_value"] for r in draw.visible.rows}

    out = {}
    for label, kwargs in (("mixed history (90% healthy, 5% gross, 5% subtle)", {}),
                          ("only blatant bugs (95% healthy, 5% gross)", {"healthy": 0.95, "gross": 0.05}),
                          ("all healthy", {"healthy": 1.0, "gross": 0.0}),
                          ("short history (60 experiments)", {"n_experiments": 60})):
        draw = srm_history(seed, **kwargs)
        p = p_values(draw)
        cal = calibrate_srm_warning(list(p.values()))
        broken = set(draw.truth["broken"])
        warned = [e for e, v in p.items() if SRM_BLOCK_ALPHA <= v < cal.warn_alpha]
        blocked = [e for e, v in p.items() if v < SRM_BLOCK_ALPHA]
        out[label] = {"experiments": len(p), "warn_alpha": cal.warn_alpha, "reason": cal.reason,
                      "blocked": len(blocked), "blocked_truly_broken": sum(e in broken for e in blocked),
                      "warned": len(warned), "warned_truly_broken": sum(e in broken for e in warned),
                      "estimated_false_warning_share": cal.estimated_false_warning_share,
                      "true_false_warning_share": (sum(e not in broken for e in warned) / len(warned)) if warned else None,
                      "broken_missed": len(broken - set(warned) - set(blocked)),
                      "false_share_at_0.01": (lambda w: sum(e not in broken for e in w) / len(w) if w else None)(
                          [e for e, v in p.items() if SRM_BLOCK_ALPHA <= v < 0.01])}
    return out
