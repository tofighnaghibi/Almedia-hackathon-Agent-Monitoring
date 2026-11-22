"""CounterfactualRolloutEngine (v3.2 §6.3; plan §I; CP-035, revised 2026-11-22). Pure; outputs are MODEL_PREDICTIONs.

Paired rollouts, with and without an intervention, over a bounded subgraph of VALIDATED mechanisms:

* bounded subgraph: the target's ancestors through registered mechanisms (lags included). A variable with no
  mechanism is exogenous. An equation is never invented (UNSUPPORTED_MODEL).
* baseline ("no change") for exogenous inputs: the mean of their last 7 observed values (user decision; "last" is
  available), or an explicit future path (used by backtests and declared plans).
* shape uncertainty: a target may have a MechanismSet (straight + curved shapes weighted by held-out fit). Each sample
  draws one member, the SAME member in both worlds, so the effect interval carries the doubt about the shape.
* common random numbers: noise keyed by (seed, node, step), parameters by (seed, mechanism, version), member choice by
  (seed, node); both worlds use identical draws and adding a node shifts nobody else's draws.
* regime: if the AVERAGE path of any input leaves the fitted range widened by a margin (default 5% of the range on each
  side; user decision), in either world, the engine abstains (OUT_OF_REGIME).
* multi-step backtest: intervals for the target are widened by its set's backtest inflation (≥ 1).
* risk constraints: P(constraint violated at some step) in the intervened world, per constraint.
* caps (CAP_EXCEEDED), reach sensitivity, emulator-fitted → other scope refused (I11).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping

import numpy as np

from ..analytics.registry import CapabilitySpec
from ..contracts.mechanisms import CounterfactualPrediction, InterventionSpec, MechanismSet, MechanismSpec

ROLLOUT = CapabilitySpec(
    id="counterfactual_rollout_v1", version="2", question_kinds=("PREDICT_OUTCOME", "COMPARE_INTERVENTIONS"),
    input_shapes=("time_series",), output_kind="MODEL_PREDICTION", estimand_families=None,
    required_metadata=("validated_mechanisms",),
    assumption_checks=("mechanisms_validated", "bounded_subgraph", "regime", "caps", "calibration_scope", "shape_set",
                       "multi_step_backtest", "risk_constraints"),
    dependence_handling="paired rollouts with common random numbers; mechanism noise iid Gaussian",
    max_work_units=5_000_000, refusal_codes=("UNSUPPORTED_MODEL", "OUT_OF_REGIME", "CAP_EXCEEDED"),
    honest_conclusion="a model prediction with intervals; never evidence, never a cause, never permission to act")

EXOGENOUS_POLICIES = ("mean_7", "last")
OPS = {"<=": np.less_equal, ">=": np.greater_equal, "<": np.less, ">": np.greater}


@dataclass(frozen=True)
class Caps:
    max_samples: int = 5_000
    max_horizon: int = 366
    max_nodes: int = 50
    max_work: int = 5_000_000


def members(entry) -> list[tuple[MechanismSpec, float]]:
    return list(zip(entry.members, entry.weights)) if isinstance(entry, MechanismSet) else [(entry, 1.0)]


def _key(*parts) -> int:
    return int.from_bytes(hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).digest()[:8], "big")


def _abstain(status, target, horizon, interventions, level, seed, n, reasons) -> CounterfactualPrediction:
    pid = "cf:" + hashlib.sha256(json.dumps([status, target, horizon, [i.model_dump() for i in interventions], seed, reasons],
                                            default=str).encode()).hexdigest()[:16]
    return CounterfactualPrediction(schema_version="2.0", prediction_id=pid, target=target, horizon_steps=horizon,
                                    interventions=tuple(interventions), status=status, reasons=tuple(reasons), level=level,
                                    seed=seed, n_samples=n)


def rollout(mechanisms: Mapping[str, MechanismSpec | MechanismSet], target: str, history: Mapping[str, list[float]],
            horizon: int, interventions: tuple[InterventionSpec, ...] = (), *, n_samples: int = 1_000, seed: int = 0,
            level: float = 0.9, caps: Caps = Caps(), target_scope_kind: str | None = None,
            reaches: tuple[float, ...] = (0.75, 0.5), exogenous_policy: str = "mean_7",
            exogenous_paths: Mapping[str, list[float]] | None = None, regime_margin: float = 0.05,
            constraints: tuple[tuple[str, str, float], ...] = (), apply_inflation: bool = True) -> CounterfactualPrediction:
    ab = lambda status, *why: _abstain(status, target, horizon, interventions, level, seed, n_samples, list(why))
    if exogenous_policy not in EXOGENOUS_POLICIES:
        raise ValueError(f"exogenous_policy must be one of {EXOGENOUS_POLICIES}")
    if target not in mechanisms:
        return ab("UNSUPPORTED_MODEL", f"no registered mechanism for {target}: an equation is never invented")
    needed, exogenous, stack = set(), set(), [target]
    while stack:
        v = stack.pop()
        if v in needed or v in exogenous:
            continue
        if v in mechanisms:
            needed.add(v)
            stack.extend(p.variable_id for m, _ in members(mechanisms[v]) for p in m.parents)
        else:
            exogenous.add(v)
    nodes = needed | exogenous
    for iv in interventions:
        if iv.variable_id not in nodes:
            return ab("UNSUPPORTED_MODEL", f"no mechanism path from {iv.variable_id} to {target}: an absent path is "
                      "unknown, not a zero effect")
    for var, op, _ in constraints:
        if var not in nodes:
            return ab("UNSUPPORTED_MODEL", f"constraint on {var} cannot be checked: it is not in the simulated subgraph")
        if op not in OPS:
            return ab("UNSUPPORTED_MODEL", f"constraint operator {op!r} is not one of {sorted(OPS)}")
    bad = sorted(f"{m.mechanism_id} ({m.status})" for v in needed for m, _ in members(mechanisms[v]) if m.status != "VALIDATED")
    if bad:
        return ab("UNSUPPORTED_MODEL", f"only VALIDATED mechanisms are rolled out; not validated: {bad}")
    kinds = sorted({m.fitted_on.scope.environment_kind for v in needed for m, _ in members(mechanisms[v])})
    if target_scope_kind is not None and "emulator" in kinds and target_scope_kind != "emulator":
        return ab("UNSUPPORTED_MODEL", "mechanisms calibrated in an emulator cannot predict for a "
                  f"{target_scope_kind} scope: a simulator fit is not real-world validation (I11)")
    paths_in = dict(exogenous_paths or {})
    missing = sorted(v for v in exogenous if not history.get(v) and v not in paths_in)
    if missing:
        return ab("UNSUPPORTED_MODEL", f"{missing} have no mechanism and no observed history: nothing to hold or simulate")
    short_paths = sorted(v for v, p in paths_in.items() if len(p) < horizon)
    if short_paths:
        return ab("UNSUPPORTED_MODEL", f"declared paths for {short_paths} are shorter than the horizon")
    work = n_samples * horizon * len(nodes) * 2
    for name, val, cap in (("samples", n_samples, caps.max_samples), ("horizon", horizon, caps.max_horizon),
                           ("nodes", len(nodes), caps.max_nodes), ("work", work, caps.max_work)):
        if val > cap:
            return ab("CAP_EXCEEDED", f"{name} {val:,} > cap {cap:,}")
    order, temp, done = [], set(), set()

    def visit(v):
        if v in done:
            return True
        if v in temp:
            return False
        temp.add(v)
        for m, _ in members(mechanisms[v]):
            for p in m.parents:
                if p.lag_steps == 0 and p.variable_id in needed and not visit(p.variable_id):
                    return False
        temp.discard(v); done.add(v); order.append(v)
        return True
    for v in sorted(needed):
        if not visit(v):
            return ab("UNSUPPORTED_MODEL", "contemporaneous (zero-lag) cycle among mechanisms: equilibrium is unsupported")
    max_lag = {v: 0 for v in nodes}
    for v in needed:
        for m, _ in members(mechanisms[v]):
            for p in m.parents:
                max_lag[p.variable_id] = max(max_lag[p.variable_id], p.lag_steps)
    short = sorted(v for v in nodes if max_lag[v] > 0 and len(history.get(v, [])) < max_lag[v])
    if short:
        return ab("UNSUPPORTED_MODEL", f"not enough history for lagged terms of {short}")

    L = max(max_lag.values()) if max_lag else 0
    n = n_samples
    theta, pick = {}, {}
    for v in needed:
        mem = members(mechanisms[v])
        for m, _ in mem:
            cov = np.array(m.covariance)
            w, U = np.linalg.eigh((cov + cov.T) / 2)
            root = U @ np.diag(np.sqrt(np.clip(w, 0, None)))
            z = np.random.default_rng(_key(seed, m.mechanism_id, m.version, "params")).standard_normal((n, len(m.coefficients)))
            theta[m.mechanism_id] = np.array(m.coefficients) + z @ root.T
        cum = np.cumsum([wt for _, wt in mem])
        u = np.random.default_rng(_key(seed, v, "member")).random(n)
        pick[v] = np.minimum(np.searchsorted(cum, u * cum[-1], side="right"), len(mem) - 1)
    hold = {v: (float(np.mean(history[v][-7:])) if exogenous_policy == "mean_7" else float(history[v][-1]))
            for v in exogenous if v not in paths_in}

    def bounds(m: MechanismSpec):
        out = {}
        for r in m.valid_regime:
            pad = regime_margin * (r.maximum - r.minimum)
            out[(r.variable_id, r.lag_steps)] = (r.minimum - pad, r.maximum + pad, r.minimum, r.maximum)
        return out

    def simulate_world(reach: float | None):
        paths = {v: np.zeros((n, L + horizon)) for v in nodes}
        for v in nodes:
            h = list(history.get(v, []))[-L:] if L else []
            if L and h:
                paths[v][:, L - len(h):L] = np.array(h, dtype=float)
        offenders = []

        def apply(v, t):
            if reach is None:
                return
            for iv in interventions:
                if iv.variable_id == v:
                    nat = paths[v][:, t]
                    paths[v][:, t] = (reach * iv.value + (1 - reach) * nat) if iv.kind == "set" else nat + reach * iv.value

        for t in range(L, L + horizon):
            for v in sorted(exogenous):
                paths[v][:, t] = float(paths_in[v][t - L]) if v in paths_in else hold[v]
                apply(v, t)
            for v in order:
                mem = members(mechanisms[v])
                preds, sds = [], []
                for m, _ in mem:
                    cols = [np.ones(n)]
                    b = bounds(m)
                    for p in m.parents:
                        x = paths[p.variable_id][:, t - p.lag_steps]
                        lo, hi, flo, fhi = b[(p.variable_id, p.lag_steps)]
                        mu = float(x.mean())
                        if mu < lo or mu > hi:
                            offenders.append(f"{p.variable_id} (lag {p.lag_steps}) for {v}: fitted on [{flo:.4g}, {fhi:.4g}] "
                                             f"(±{regime_margin:.0%} margin), average path reaches {mu:.4g}")
                        cols.append(x * x if p.transform == "square" else x)
                    preds.append((theta[m.mechanism_id] * np.column_stack(cols)).sum(axis=1))
                    sds.append(m.noise_sd)
                idx = pick[v]
                eps = np.random.default_rng(_key(seed, v, t - L)).standard_normal(n)
                paths[v][:, t] = np.choose(idx, preds) + np.array(sds)[idx] * eps
                apply(v, t)
        return paths, offenders

    base, ob = simulate_world(None)
    world, oi = simulate_world(1.0) if interventions else (base, ob)
    if ob or oi:
        uniq = list(dict.fromkeys(oi + ob))
        return ab("OUT_OF_REGIME", f"{len(oi) + len(ob)} mechanism input(s) leave the fitted range on average", *uniq[:3])

    k = (mechanisms[target].multi_step.inflation if apply_inflation and isinstance(mechanisms[target], MechanismSet)
         and mechanisms[target].multi_step is not None else 1.0)
    q = ((1 - level) / 2, (1 + level) / 2)

    def summ(x):
        lo, hi = np.quantile(x, q)
        m = float(x.mean())
        return {"mean": m, "lower": float(m - k * (m - lo)), "upper": float(m + k * (hi - m))}
    tb, ti = base[target][:, L:].sum(axis=1), world[target][:, L:].sum(axis=1)
    sens = {}
    for r in reaches if interventions else ():
        w_r, _ = simulate_world(r)
        sens[f"reach {r:.0%}"] = summ(w_r[target][:, L:].sum(axis=1) - tb)
    risk = {f"{var} {op} {val:g}": float((~OPS[op](world[var][:, L:], val)).any(axis=1).mean()) for var, op, val in constraints}
    versions = {m.mechanism_id: m.version for v in sorted(needed) for m, _ in members(mechanisms[v])}
    weights = {v: [round(wt, 3) for _, wt in members(mechanisms[v])] for v in sorted(needed) if len(members(mechanisms[v])) > 1}
    exo_text = (f"exogenous inputs {sorted(exogenous)}: " +
                ", ".join(f"{v} {'follows a declared path' if v in paths_in else ('held at its 7-day mean' if exogenous_policy == 'mean_7' else 'held at its last value')}"
                          for v in sorted(exogenous)))
    pid = "cf:" + hashlib.sha256(json.dumps([target, horizon, [i.model_dump() for i in interventions], seed, n, versions,
                                             {v: list(history.get(v, []))[-7:] for v in sorted(nodes)}, exogenous_policy,
                                             regime_margin, constraints, {v: list(p)[:horizon] for v, p in paths_in.items()},
                                             k], default=str).encode()).hexdigest()[:16]
    body = {"schema_version": "2.0", "prediction_id": pid, "target": target, "horizon_steps": horizon,
            "interventions": [i.model_dump(mode="json") for i in interventions], "status": "PREDICTED", "level": level,
            "baseline": summ(tb), "intervened": summ(ti) if interventions else None,
            "effect": summ(ti - tb) if interventions else None, "reach_sensitivity": sens, "constraint_risk": risk,
            "mechanism_versions": versions, "calibration_scope_kinds": kinds, "seed": seed, "n_samples": n,
            "assumptions": ["each mechanism keeps its form under the intervention (modularity)",
                            "mechanism parents are not confounded with its noise (fitted on exogenous variation)",
                            exo_text,
                            "Gaussian noise with the fitted variance; parameters drawn from their fitted covariance",
                            (f"equation shape uncertain: shapes weighted {weights} by held-out fit" if weights else
                             "single equation shape per mechanism"),
                            f"intervals widened ×{k:.2f} by the multi-step backtest" if k > 1 else "multi-step backtest inflation 1.00",
                            f"regime: fitted range ±{regime_margin:.0%}",
                            f"target summed over {horizon} step(s)"]}
    return CounterfactualPrediction.model_validate_json(json.dumps(body))
