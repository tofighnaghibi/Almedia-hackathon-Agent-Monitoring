"""Phase 7 harness (evaluation side): register mechanisms on emulator worlds, run what-if questions through the core,
and score predictions against the hidden true counterfactual. Held-out seeds, a held-out mechanism family and an
out-of-regime intervention. The core never imports this module.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np

from adapters.outputs.sqlite_sinks import IdempotentSink
from adapters.sources.canonical_port import CanonicalEvidencePort
from causal_core import api
from causal_core.contracts.mechanisms import InterventionSpec, MechanismSpec
from causal_core.counterfactual.mechanisms import fit_mechanism, validate_mechanism
from causal_core.counterfactual.rollouts import rollout
from causal_core.orchestration.coordinator import TaskCoordinator
from causal_core.orchestration.planner import TaskContext
from causal_core.storage.ledger import Ledger
from emulator import dynamics_world as D

STRUCTURE = {"visitors": [("spend", 0)], "trials": [("visitors", 0), ("trials", 1)]}   # declared from the graph


def register(world: D.DynamicsWorld, fit_days: int = 120, *, shapes: bool = True, backtest: bool = True) -> dict:
    """Fit on days 1..fit_days-1, validate on the rest. shapes=True: straight + curved shapes weighted by held-out fit
    (CP-034 fix); shapes=False: the straight line only (the Phase 7 v1 behaviour, kept for comparison)."""
    from causal_core.counterfactual.mechanisms import backtest_set, fit_shape_set
    ts = sorted(world.series["spend"])
    out = {}
    for target, parents in STRUCTURE.items():
        if shapes:
            mset, _ = fit_shape_set(world.series, target, parents, mechanism_id=f"m:{target}", version=1, scope=D.scope(),
                                    lineage_roots=(f"{D.SOURCE}:{target}",), fit_start=ts[1], fit_end=ts[fit_days - 1],
                                    val_start=ts[fit_days], val_end=ts[-1])
            if mset is None:
                continue
            out[target] = mset
        else:
            m = fit_mechanism(world.series, target, parents, mechanism_id=f"m:{target}", version=1, scope=D.scope(),
                              lineage_roots=(f"{D.SOURCE}:{target}",), start=ts[1], end=ts[fit_days - 1])
            out[target] = validate_mechanism(m, world.series, start=ts[fit_days], end=ts[-1])
    if shapes and backtest and "trials" in out:
        out["trials"] = backtest_set(out, world.series, "trials", start=ts[fit_days + 7], end=ts[-1])
    return out


def history(world: D.DynamicsWorld, k: int = 7) -> dict[str, list[float]]:
    ts = sorted(world.series["spend"])[-k:]
    return {v: [world.series[v][t] for t in ts] for v in world.series}


def harness(world: D.DynamicsWorld, mechanisms: dict | None = None, *, samples: int = 2000):
    d = Path(tempfile.mkdtemp(prefix="p7-"))
    ledger = Ledger(d / "ledger.db")
    ctx = TaskContext(mechanisms=register(world) if mechanisms is None else mechanisms, rollout_samples=samples, rollout_seed=7)
    co = TaskCoordinator(ledger, CanonicalEvidencePort(world.observations, "emu-dynamics-port"), IdempotentSink(d / "sink.db"),
                         context=ctx)
    return co, ledger


def task(tid: str, question: dict, trigger: str = "explicit_question", scope: dict | None = None):
    cut = "2026-11-20T00:00:00Z"
    return api.parse_reasoning_task({"schema_version": "2.0", "task_id": tid, "trigger": trigger,
                                     "source_scope": scope or D.scope(), "priority": 5, "data_cut": {"as_of": cut},
                                     "budget": {"max_wall_ms": 30_000}, "created_at": cut, "question": question})


Q_COMPARE = {"question_kind": "COMPARE_INTERVENTIONS", "candidates": ["spend=130", "spend=150", "spend=300"],
             "utility": "maximize trials over 14d", "risk_constraints": []}
Q_FORECAST = {"question_kind": "PREDICT_OUTCOME", "target": {"variable_id": "trials", "horizon": "14d"}}


def calibration_study(seeds=range(100, 140), families=("linear", "saturating"), levels=(0.5, 0.8, 0.9, 0.95),
                      spend: float = 130.0, horizon: int = 14, samples: int = 1500, shapes: bool = True) -> dict:
    """Held-out seeds (never used to design the method) per family: does the stated interval cover the true effect?"""
    out = {}
    for fam in families:
        hits = {lv: 0 for lv in levels}
        n = validated = abstained = 0
        errs = []
        for sd in seeds:
            w = D.build(seed=sd, family=fam)
            try:
                mech = register(w, shapes=shapes)
            except ValueError:
                continue
            if set(mech) != set(STRUCTURE) or (not shapes and not all(m.status == "VALIDATED" for m in mech.values())):
                continue
            validated += 1
            truth = D.true_effect(w, spend, horizon)["effect"]
            preds = [rollout(mech, "trials", history(w), horizon, (InterventionSpec(variable_id="spend", kind="set", value=spend),),
                             n_samples=samples, seed=sd, level=lv, target_scope_kind="emulator") for lv in levels]
            if preds[0].status != "PREDICTED":
                abstained += 1
                continue
            for lv, p in zip(levels, preds):
                hits[lv] += p.effect.lower <= truth <= p.effect.upper
                if lv == 0.9:
                    errs.append(p.effect.mean - truth)
            n += 1
        out[fam] = {"worlds": len(list(seeds)), "validated": validated, "predicted": n, "abstained": abstained,
                    "coverage": {str(lv): hits[lv] / max(n, 1) for lv in levels},
                    "mean_error": float(np.mean(errs)) if errs else None, "rmse": float(np.sqrt(np.mean(np.square(errs)))) if errs else None}
    return out


def out_of_regime_study(seed: int = 3, spend: float = 300.0, horizon: int = 14) -> dict:
    """What the guard prevents: the engine abstains. Evaluation-only copies with the guard switched off show what an
    extrapolation would have said: the straight line alone, and the straight + curved shape set."""
    def unguarded(mech, w, iv):
        wide = {}
        for k, m in mech.items():
            body = m.model_dump(mode="json")
            for spec in (body["members"] if "members" in body else [body]):
                for rb in spec["valid_regime"]:
                    rb["minimum"], rb["maximum"] = -1e12, 1e12
            wide[k] = type(m).model_validate_json(json.dumps(body))
        return rollout(wide, "trials", history(w), horizon, iv, n_samples=1500, seed=seed, target_scope_kind="emulator")
    out = {}
    for fam in ("linear", "saturating"):
        w = D.build(seed=seed, family=fam)
        mech = register(w)
        iv = (InterventionSpec(variable_id="spend", kind="set", value=spend),)
        p = rollout(mech, "trials", history(w), horizon, iv, n_samples=1500, seed=seed, target_scope_kind="emulator")
        line, both = unguarded(register(w, shapes=False), w, iv), unguarded(mech, w, iv)
        truth = D.true_effect(w, spend, horizon)["effect"]
        out[fam] = {"status": p.status, "reason": p.reasons[0] if p.reasons else "", "truth": truth,
                    "unguarded_prediction": line.effect.mean, "unguarded_interval": [line.effect.lower, line.effect.upper],
                    "unguarded_covers_truth": line.effect.lower <= truth <= line.effect.upper,
                    "unguarded_shapes_interval": [both.effect.lower, both.effect.upper],
                    "unguarded_shapes_covers_truth": both.effect.lower <= truth <= both.effect.upper}
    return out


def crn_study(seed: int = 3, spend: float = 130.0, horizon: int = 14) -> dict:
    """Paired (common random numbers) effect interval vs the interval if the two worlds had independent noise
    (approximated from the two worlds' own intervals)."""
    w = D.build(seed=seed, family="linear")
    mech = register(w)
    p = rollout(mech, "trials", history(w), horizon, (InterventionSpec(variable_id="spend", kind="set", value=spend),),
                n_samples=2000, seed=seed, target_scope_kind="emulator")
    paired = p.effect.upper - p.effect.lower
    indep = float(np.hypot(p.baseline.upper - p.baseline.lower, p.intervened.upper - p.intervened.lower))
    return {"paired_width": paired, "independent_width_approx": indep, "ratio": paired / indep}
