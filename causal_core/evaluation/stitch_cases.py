"""Stitch marketplace through the core (evaluation side; CP-038). Reads marketplace-recon's sample output via the
Stitch adapter and runs what the samples can support: exact ratio identities, the Hillstrom A/B sample, and the
change monitor on service telemetry. The samples are conformance-sized (9 weekly points per metric), so the
analyses are demonstrations of behaviour, not findings about the marketplace.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from adapters.outputs.sqlite_sinks import IdempotentSink
from adapters.sources.stitch_port import StitchEvidencePort, StitchLoad, load_stitch
from causal_core import api
from causal_core.orchestration.coordinator import TaskCoordinator
from causal_core.orchestration.planner import MonitorSpec, TaskContext
from causal_core.semantics.metrics import verify_identity
from causal_core.storage.ledger import Ledger

DEFAULT_ZIP = Path(__file__).resolve().parents[2] / "stitch_samples.zip"


def series(load: StitchLoad, var: str) -> dict:
    """time → value for a variable's time_series samples (first sample's scope)."""
    out = {}
    for o in load.observations:
        if o["variable_id"] == var and o["payload"]["shape"] == "time_series":
            for p in o["payload"]["points"]:
                out[p["t"]] = p["value"]
    return out


def identities(load: StitchLoad) -> list[dict]:
    """Every ratio definition with a formula: does ratio == numerator / denominator in each shared window?"""
    rows_out = []
    for vid, d in sorted(load.definitions.items()):
        f = getattr(d, "formula_ast", None)
        if f is None:
            continue
        refs = [f.left.variable_id, f.right.variable_id] if hasattr(f, "left") else []
        data = {v: series(load, v) for v in [vid] + refs}
        keys = sorted(set.intersection(*(set(x) for x in data.values()))) if all(data.values()) else []
        rows = [(k, {v: data[v][k] for v in data}) for k in keys]
        tol = getattr(d, "identity_tolerance", None) or 1e-4
        r = verify_identity(f, vid, rows, tolerance=tol, min_windows=2)
        rows_out.append({"ratio": vid, "numerator": refs[0] if refs else None, "denominator": refs[1] if refs else None,
                         "windows": r.windows_checked, "verdict": r.verdict, "max_abs_residual": r.max_abs_residual,
                         "excluded": len(r.excluded), "scope": d.scope.environment_id})
    return rows_out


def coordinator(load: StitchLoad, *, studies=None, monitors=None):
    d = Path(tempfile.mkdtemp(prefix="stitch-"))
    ctx = TaskContext(studies=studies or {}, monitors=monitors or {})
    return TaskCoordinator(Ledger(d / "ledger.db"), StitchEvidencePort(load), IdempotentSink(d / "sink.db"), context=ctx)


def hillstrom_task(load: StitchLoad, treatment="Womens_E-Mail", control="No_E-Mail", tid="stitch-hillstrom"):
    scope = load.definitions["experiment_conversion"].scope.model_dump(mode="json")
    est = {"outcome": "experiment_conversion", "contrast": {"treatment": treatment, "control": control},
           "population": "Hillstrom email recipients (sample)", "horizon": "2 weeks", "functional": "ITT",
           "source_scope": scope, "target_scope": scope}
    cut = "2026-11-22T00:00:00Z"
    return api.parse_reasoning_task({"schema_version": "2.0", "task_id": tid, "trigger": "explicit_question",
                                     "source_scope": scope, "priority": 5, "data_cut": {"as_of": cut},
                                     "budget": {"max_wall_ms": 20_000}, "created_at": cut,
                                     "question": {"question_kind": "ESTIMATE_EFFECT", "estimand": est,
                                                  "study_id": "hillstrom-email-2008"}})


def hillstrom_design(load: StitchLoad):
    """The published Hillstrom design: users randomized 1/3 each to two e-mail campaigns or no e-mail. Declared here
    (from the dataset's documentation), not inferred from the outcomes."""
    return api.parse_study_design({
        "schema_version": "2.0", "design": "randomized", "study_id": "hillstrom-email-2008",
        "scope": load.definitions["experiment_conversion"].scope.model_dump(mode="json"),
        "provenance": {"source_id": "hillstrom", "method": "adapter_declared"}, "population": "e-mail customers",
        "outcomes": ["experiment_conversion"], "randomization": "simple", "assignment_unit": "customer",
        "outcome_unit": "customer", "expected_allocation": {"Womens_E-Mail": 1 / 3, "Mens_E-Mail": 1 / 3, "No_E-Mail": 1 / 3},
        "compliance": "unknown", "interference": "none_assumed"})


def monitor_task(load: StitchLoad, var: str, tid: str):
    scope = load.definitions[var].scope.model_dump(mode="json")
    cut = "2026-11-22T00:00:00Z"
    return api.parse_reasoning_task({"schema_version": "2.0", "task_id": tid, "trigger": "monitor", "source_scope": scope,
                                     "priority": 5, "data_cut": {"as_of": cut}, "budget": {"max_wall_ms": 20_000},
                                     "created_at": cut, "incident_id": f"inc-{tid}",
                                     "question": {"question_kind": "DETECT_CHANGE", "variables": [var],
                                                  "baseline_policy": "declared null"}})


def telemetry_monitor(load: StitchLoad, var: str, alpha: float = 0.05) -> dict:
    """Freeze the null on the first half (older window), test the second half. Dependence is checked on the
    training half (lag-1 autocorrelation beyond 2/√n → declared 'ar1', and the method refuses)."""
    import math
    import numpy as np
    from causal_core.analytics.sequential import evaluate_eprocess
    pts = sorted(series(load, var).items())
    vals = np.array([v for _, v in pts if v is not None], dtype=float)
    half = len(vals) // 2
    train, live = vals[:half], vals[half:]
    mu, sd = float(train.mean()), float(train.std(ddof=1))
    r1 = float(np.corrcoef(train[:-1], train[1:])[0, 1]) if sd > 0 else 0.0
    dependence = "independent" if abs(r1) < 2 / math.sqrt(half) else "ar1"
    r = evaluate_eprocess(list((live - mu) / sd) if sd > 0 else [0.0], null_spec=f"N({mu:.4g}, {sd:.4g}) from first half",
                          dependence=dependence, alpha=alpha)
    return {"variable": var, "points": len(vals), "train_mean": mu, "train_sd": sd, "lag1_autocorrelation": r1,
            "dependence": dependence, "status": r.status, "refusal": r.refusal_code,
            "alarm": r.values.get("alarm") if r.status != "REFUSED" else None,
            "first_crossing_index": r.values.get("first_crossing_index") if r.status != "REFUSED" else None,
            "direction": r.values.get("direction") if r.status != "REFUSED" else None}
