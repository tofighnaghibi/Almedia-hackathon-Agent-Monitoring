"""Statsig adapter harness (evaluation side): synthetic Statsig-shaped fixtures for offline tests, wiring into the
coordinator, and a cross-check of the core's recomputation against Statsig's own reported numbers.

No real Babbel data is stored here: the fixture below is invented. Real data is read only by scripts/statsig_smoke.py,
which prints to the terminal and writes nothing into the repository.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from adapters.outputs.sqlite_sinks import IdempotentSink
from adapters.sources.statsig_port import SCOPE, ReadOnlyStatsigClient, StatsigEvidencePort, design_from_statsig, slug
from causal_core import api
from causal_core.analytics.experiments import ArmSummary, evaluate_randomized_summary
from causal_core.orchestration.coordinator import TaskCoordinator
from causal_core.orchestration.planner import TaskContext
from causal_core.storage.ledger import Ledger

EXP = "demo_exp_1"
METRIC_ID = "trial start rate (d7)::user_warehouse"
FILTERED_ID = "revenue per purchaser::user_warehouse"


def fixture_cache(groups=(("Control", 50, True), ("Variant A", 50, False)), units=(49_900, 50_100),
                  means=(0.1000, 0.1100), ses=(0.0030, 0.0040), status="decision_made", cached_at="2026-11-22T05:00:00",
                  filtered_metric_units: tuple[int, int] | None = None) -> Path:
    """An invented experiment in the hackathon cache layout. Statsig's reported delta/CI are computed consistently."""
    d = Path(tempfile.mkdtemp(prefix="statsig-fixture-"))
    e = d / "experiments" / EXP
    e.mkdir(parents=True)
    details = {"id": EXP, "name": EXP, "idType": "userID", "status": status, "startTime": 1762000000000,
               "groups": [{"name": n, "id": f"g{i}", "size": s, "isControl": c} for i, (n, s, c) in enumerate(groups)],
               "primaryMetrics": [{"name": "trial start rate (d7)", "type": "user_warehouse"}], "secondaryMetrics": []}
    se = (ses[0] ** 2 + ses[1] ** 2) ** 0.5
    delta = means[1] - means[0]
    pulse = {"ds": "2026-11-20", "secondaryMetrics": [], "primaryMetrics": [{
        "metricID": METRIC_ID, "metricName": "trial start rate (d7)", "absoluteChange": delta,
        "confidenceInterval": {"lower": delta - 1.959963984540054 * se, "upper": delta + 1.959963984540054 * se},
        "adjustedAlpha": 0.05, "pValue": None, "testMean": means[1], "controlMean": means[0], "testStd": ses[1],
        "controlStd": ses[0], "testUnits": units[1], "controlUnits": units[0]}]}
    if filtered_metric_units:                    # a metric that only counts a subset of users (e.g. purchasers)
        fc, ft = filtered_metric_units
        pulse["secondaryMetrics"].append({
            "metricID": FILTERED_ID, "metricName": "revenue per purchaser", "absoluteChange": 0.5,
            "confidenceInterval": {"lower": -0.5, "upper": 1.5}, "adjustedAlpha": 0.05, "pValue": None,
            "testMean": 30.5, "controlMean": 30.0, "testStd": 0.4, "controlStd": 0.35, "testUnits": ft, "controlUnits": fc})
    (e / "details.json").write_text(json.dumps(details))
    (e / "pulse_cuped.json").write_text(json.dumps(pulse))
    (d / "cache_manifest.json").write_text(json.dumps({"entries": {
        f"experiments/{EXP}/details": {"cached_at": cached_at}, f"experiments/{EXP}/pulse_cuped": {"cached_at": cached_at}}}))
    return d


class FixtureLiveClient(ReadOnlyStatsigClient):
    """Live semantics (the caller's group ids are sent) served from fixture files, so tests need no network."""

    def __init__(self, fixture_dir: Path):
        super().__init__(token="fixture-token")
        self._fx = ReadOnlyStatsigClient(cache_dir=fixture_dir)
        self.pulse_requests: list[tuple[str, str]] = []

    def experiment(self, experiment_id):
        return self._fx.experiment(experiment_id)

    def pulse(self, experiment_id, control_id, test_id, cuped=True):
        self.pulse_requests.append((control_id, test_id))
        return self._fx.pulse(experiment_id, control_id, test_id, cuped)


def harness(cache_dir: Path, *, contrasts=None, live_semantics: bool = True, exposure_counts=None):
    client = FixtureLiveClient(cache_dir) if live_semantics else ReadOnlyStatsigClient(cache_dir=cache_dir)
    port = StatsigEvidencePort(client, contrasts=contrasts, exposure_counts=exposure_counts)
    details, _ = client.experiment(EXP)
    d = Path(tempfile.mkdtemp(prefix="statsig-run-"))
    ctx = TaskContext(studies={slug(EXP): api.parse_study_design(design_from_statsig(details))})
    return TaskCoordinator(Ledger(d / "l.db"), port, IdempotentSink(d / "s.db"), context=ctx), port


def effect_task(task_id: str, experiment: str, metric_slug: str, treatment: str, control: str, cut: str,
                functional: str = "ITT"):
    est = {"outcome": metric_slug, "contrast": {"treatment": treatment, "control": control},
           "population": f"Statsig units in {experiment}", "horizon": "experiment window", "functional": functional,
           "source_scope": SCOPE, "target_scope": SCOPE}
    return api.parse_reasoning_task({
        "schema_version": "2.0", "task_id": task_id, "trigger": "explicit_question", "source_scope": SCOPE, "priority": 5,
        "data_cut": {"as_of": cut}, "budget": {"max_wall_ms": 30_000}, "created_at": cut,
        "question": {"question_kind": "ESTIMATE_EFFECT", "estimand": est, "study_id": slug(experiment)}})


def cross_check(port: StatsigEvidencePort, design, experiment: str, metric_slug: str, treatment: str, control: str,
                response) -> dict:
    """Recompute at Statsig's own level (1 − adjustedAlpha) and compare with what Statsig reported."""
    rep = port.source_reported[(slug(experiment), metric_slug)]
    arms = {next(x.value for x in o.segment if x.dimension_id == "arm"): ArmSummary(o.payload.n, o.payload.mean, o.payload.se)
            for o in response if o.variable_id == metric_slug}
    level = 1 - float(rep["adjustedAlpha"]) if rep.get("adjustedAlpha") is not None else 0.95
    r = evaluate_randomized_summary(design, arms, treatment, control, level=level)
    ours = r.values if r.status != "REFUSED" else {}
    ci = rep.get("confidenceInterval") or {}
    return {"level": level, "statsig_delta": rep.get("absoluteChange"), "core_delta": ours.get("estimate"),
            "statsig_ci": [ci.get("lower"), ci.get("upper")], "core_ci": ours.get("ci"),
            "max_abs_diff": max(abs(ours["estimate"] - float(rep["absoluteChange"])),
                                abs(ours["ci"][0] - float(ci["lower"])), abs(ours["ci"][1] - float(ci["upper"])))
            if ours and ci else None, "core_status": r.status, "core_refusal": r.refusal_code}
