"""Use case 1 (demo-only): an autonomous experiment watcher on Statsig-shaped data, acting through a sandbox.

Board = 9 real experiments from the local Statsig cache (anonymized EXP-01..EXP-09, re-derived by the core) +
3 simulated experiments with injected incidents and known truth (EXP-10..EXP-12). The agent wakes on Statsig's real
load cadence (daily load ~03:50 UTC → wake 04:00; scheduled Pulse reloads → wake 14:15), runs core tasks, and turns
findings into proposals. A scripted on-call approver approves; the mock Statsig project executes; receipts follow.

    cd causal_core_demo_video && python3 -m agent.uc1        # writes outputs/uc1/
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
import tempfile
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
CORE = HERE.parent / "causal_core"
sys.path.insert(0, str(CORE))

import numpy as np  # noqa: E402

from adapters.outputs.sqlite_sinks import IdempotentSink  # noqa: E402
from adapters.sources.canonical_port import CanonicalEvidencePort  # noqa: E402
from adapters.sources.statsig_port import ReadOnlyStatsigClient, StatsigEvidencePort, design_from_statsig, slug  # noqa: E402
from causal_core import api  # noqa: E402
from causal_core.analytics.descriptive import BucketQuality, evaluate_dq  # noqa: E402
from causal_core.analytics.sequential import log_wealth_path  # noqa: E402
from causal_core.contracts.capabilities import CapabilityResponse  # noqa: E402
from causal_core.orchestration.coordinator import TaskCoordinator  # noqa: E402
from causal_core.orchestration.planner import MonitorSpec, TaskContext  # noqa: E402
from causal_core.storage.ledger import Ledger  # noqa: E402
from evaluation import statsig_cases as SC  # noqa: E402

from .sandbox import ActionProposal, Approval, MockStatsigProject  # noqa: E402

OUT = HERE / "outputs" / "uc1"
CACHE = HERE.parent / "hackathon" / ".statsig_cache"
START = datetime(2026, 10, 31, tzinfo=timezone.utc)     # replay window ends 21 Nov 2026
DAYS = 21
N11, REL11 = 25_000, -0.05                     # EXP-11: users per arm per day; relative harm to trial starts
SCOPE = {"environment_id": "demo-experiments", "environment_kind": "emulator", "generator_version": "demo-uc1/0.1"}
GEN = "demo-uc1/0.1"
APPROVER = "on-call product lead"


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# ── simulated experiments with injected incidents ─────────────────────────────────────────────────────


def _obs(var, key, payload, day_end, available, segment=None, dq=None):
    h = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    o = {"schema_version": "2.0", "observation_id": f"o:{key}", "variable_id": var, "payload": payload, "source_id": "demo-statsig",
         "record_key": key, "revision": 1, "scope": SCOPE,
         "lineage": {"source_id": "demo-statsig", "origin_kind": "synthetic_generated", "lineage_root_id": f"demo:{key}",
                     "content_hash": h, "generator_version": GEN},
         "clocks": {"observed_at": {"quality": "KNOWN", "value": iso(day_end)}, "available_at": {"quality": "KNOWN", "value": iso(available)}}}
    if segment:
        o["segment"] = [{"dimension_id": "arm", "value": segment}]
    if dq:
        o["dq"] = dq
    return o


def simulate(seed: int = 11, stop_day: dict | None = None) -> dict:
    """Daily data for EXP-10/11/12. stop_day: {"EXP-11": d} → variant receives no new users from day d (sandbox action)."""
    rng = np.random.default_rng(seed)
    stop_day = stop_day or {}
    obs, truth = [], {}
    # EXP-10: user-ID bucket bug from day 3: 4% of daily traffic evaluated before the user ID is set → all to control
    cum = {"control": 0, "variant": 0}; missing = {"control": 0, "variant": 0}; s = {"control": [0.0, 0.0], "variant": [0.0, 0.0]}
    srm_path = []
    for d in range(DAYS):
        day_end = START + timedelta(days=d + 1); avail = day_end + timedelta(hours=3, minutes=50)
        n = {a: int(rng.normal(10_000, 150)) for a in cum}
        bug = int(0.04 * (n["control"] + n["variant"])) if d >= 2 else 0
        n["control"] += bug; missing["control"] += bug
        for a in cum:
            cum[a] += n[a]
            y = rng.binomial(n[a], 0.31); s[a][0] += y; s[a][1] += y
        for a in cum:
            p = s[a][0] / cum[a]
            obs.append(_obs("lesson_completion", f"EXP-10:lesson_completion:{a}:{d}",
                            {"shape": "aggregate_stats", "n": cum[a], "mean": p, "se": math.sqrt(p * (1 - p) / cum[a])}, day_end, avail, a))
            obs.append(_obs("assignment_units", f"EXP-10:assignment_units:{a}:{d}", {"shape": "aggregate_stats", "n": cum[a],
                                                                                     "total": float(cum[a])}, day_end, avail, a))
        srm_path.append({"day": d + 1, "control": cum["control"], "variant": cum["variant"],
                         "missing_id_control": missing["control"], "missing_id_variant": missing["variant"]})
    truth["EXP-10"] = {"incident": "users evaluated before their user ID is set hash to one bucket (control) from day 3",
                       "share_of_traffic": 0.04, "true_effect": 0.0}
    # EXP-11: variant lowers trial starts by 3% relative from day 1; daily z of the difference feeds the monitor
    base, rel = 0.08, REL11
    cum = {"control": 0, "variant": 0}; conv = {"control": 0, "variant": 0}; zs = []; daily_total = []
    for d in range(DAYS):
        day_end = START + timedelta(days=d + 1); avail = day_end + timedelta(hours=3, minutes=50)
        stopped = "EXP-11" in stop_day and d >= stop_day["EXP-11"]
        n = {"control": N11, "variant": 0 if stopped else N11}
        if stopped:
            n["control"] = 2 * N11
        y = {"control": rng.binomial(n["control"], base), "variant": rng.binomial(n["variant"], base * (1 + rel)) if n["variant"] else 0}
        daily_total.append({"day": d + 1, "trial_starts": int(y["control"] + y["variant"]), "variant_live": not stopped})
        if not stopped:
            pc, pv = y["control"] / n["control"], y["variant"] / n["variant"]
            se = math.sqrt(pc * (1 - pc) / n["control"] + pv * (1 - pv) / n["variant"])
            z = (pv - pc) / se
            zs.append({"day": d + 1, "z": z})
            obs.append(_obs("trial_start_diff_z", f"EXP-11:z:{d}", {"shape": "time_series", "points": [{"t": iso(day_end), "value": z}]},
                            day_end, avail))
        for a in cum:
            cum[a] += n[a]; conv[a] += y[a]
        if not stopped:
            for a in cum:
                p = conv[a] / cum[a]
                obs.append(_obs("trial_starts", f"EXP-11:trial_starts:{a}:{d}",
                                {"shape": "aggregate_stats", "n": cum[a], "mean": p, "se": math.sqrt(p * (1 - p) / cum[a])}, day_end, avail, a))
                obs.append(_obs("assignment_units", f"EXP-11:assignment_units:{a}:{d}", {"shape": "aggregate_stats", "n": cum[a],
                                                                                         "total": float(cum[a])}, day_end, avail, a))
    truth["EXP-11"] = {"incident": f"variant B lowers trial starts by {-rel:.0%} relative", "true_effect_per_user": base * rel,
                       "trial_starts_lost_per_day_while_live": N11 * base * -rel}
    # EXP-12: healthy; same-day numbers are partial until the next daily load
    daily = [float(rng.normal(52_000, 900)) for _ in range(DAYS)]
    truth["EXP-12"] = {"incident": "none: same-day data is partial until the 03:50 UTC daily load", "daily_lesson_starts": daily}
    return {"observations": obs, "truth": truth, "srm_path": srm_path, "z_path": zs, "daily_trial_starts": daily_total,
            "exp12_daily": daily}


class DemoPort(CanonicalEvidencePort):
    """Serves the latest available cumulative arm summary per (variable, arm) for a study (demo-only)."""

    def query(self, request):
        if request.request != "get_assignment_outcomes":
            return super().query(request)
        fp = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        best = {}
        for o in self._obs:
            if not o.record_key.startswith(f"{request.study_id}:") or o.payload.shape != "aggregate_stats":
                continue
            if o.variable_id not in request.variables and o.variable_id != "assignment_units":
                continue
            if not (o.clocks.available_at.value <= request.data_cut):
                continue
            k = (o.variable_id, o.segment[0].value)
            if k not in best or o.clocks.observed_at.value > best[k].clocks.observed_at.value:
                best[k] = o
        return CapabilityResponse(schema_version="2.0", request_id=request.request_id, request_fingerprint=fp, status="OK",
                                  observations=tuple(best.values()))


def design(exp: str, outcome: str):
    return api.parse_study_design({"schema_version": "2.0", "design": "randomized", "study_id": exp, "scope": SCOPE,
                                   "provenance": {"source_id": "demo-statsig", "method": "adapter_declared"},
                                   "population": "app users", "outcomes": [outcome], "randomization": "simple",
                                   "assignment_unit": "userID", "outcome_unit": "userID",
                                   "expected_allocation": {"control": 0.5, "variant": 0.5}, "compliance": "unknown",
                                   "interference": "none_assumed"})


def task(tid, question, cut: datetime, trigger="scheduled", scope=SCOPE):
    raw = {"schema_version": "2.0", "task_id": tid, "trigger": trigger, "source_scope": scope, "priority": 5,
           "data_cut": {"as_of": iso(cut)}, "budget": {"max_wall_ms": 20_000}, "created_at": iso(cut), "question": question}
    if trigger == "monitor":
        raw["incident_id"] = f"inc-{tid}"
    return api.parse_reasoning_task(raw)


def effect_q(exp, outcome, scope=SCOPE, treatment="variant", control="control"):
    return {"question_kind": "ESTIMATE_EFFECT", "study_id": exp, "estimand": {
        "outcome": outcome, "contrast": {"treatment": treatment, "control": control}, "population": "app users",
        "horizon": "experiment window", "functional": "ITT", "source_scope": scope, "target_scope": scope}}


# ── real backdrop from the local Statsig cache (anonymized) ───────────────────────────────────────────


def metric_label(name: str) -> str:
    n = name.lower()
    for keys, label in ((("caris", "revenue", "proceeds", "price"), "Revenue per user"), (("trial",), "Trial starts"),
                        (("registr", "signup", "sign-up"), "Registrations"), (("active learning",), "Active learning days"),
                        (("lesson complet",), "Lesson completers"), (("convo", "conversation"), "Conversation completions"),
                        (("reload",), "Home screen reloads"), (("retention", "return"), "Retention"),
                        (("purchase", "conversion"), "Purchase conversion")):
        if any(k in n for k in keys):
            return label
    return "Primary metric"


def backdrop(n: int = 9) -> list[dict]:
    client = ReadOnlyStatsigClient(cache_dir=CACHE)
    rows, areas = [], {}
    for exp in sorted(p.name for p in (CACHE / "experiments").iterdir() if p.is_dir()):
        if len(rows) >= n:
            break
        try:
            det, _ = client.experiment(exp)
            pulse, _ = client.pulse(exp, "", "")
        except FileNotFoundError:
            continue
        groups = [g for g in det.get("groups", []) if not g.get("disabled")]
        prim = pulse.get("primaryMetrics") or []
        if det.get("status") != "decision_made" or len(groups) != 2 or not prim or prim[0].get("testUnits") is None:
            continue
        port = StatsigEvidencePort(client)
        pair = port._contrast(exp, groups)
        if isinstance(pair, str):
            continue
        control, test = pair
        d = Path(tempfile.mkdtemp(prefix="uc1-bd-"))
        dsg = api.parse_study_design(design_from_statsig(det))
        co = TaskCoordinator(Ledger(d / "l.db"), port, IdempotentSink(d / "s.db"), context=TaskContext(studies={slug(exp): dsg}))
        var = slug(prim[0]["metricID"])
        r = co.run(SC.effect_task(f"bd-{len(rows)}", exp, var, slug(test["name"]), slug(control["name"]), "2026-11-22T00:00:00Z"))
        res = next((x for m, x in r.state.results if m == "randomized_effect_v1"), None)
        obs = [o for resp in r.state.responses for o in resp.observations if o.variable_id == var]
        x = SC.cross_check(port, dsg, exp, var, slug(test["name"]), slug(control["name"]), obs) if obs and res and res.status != "REFUSED" else None
        area = areas.setdefault(det.get("team") or "-", f"Area {chr(65 + len(areas))}")
        claim = r.state.claims[0] if r.state.claims else None
        rows.append({"id": f"EXP-{len(rows) + 1:02d}", "area": area, "metric": metric_label(prim[0].get("metricName", "")),
                     "status": ("HEALTHY" if r.state.outcome == "COMPLETED" else "INVALID" if any(f.cls == "STATISTICAL_INVALIDITY" for f in r.state.failures)
                                else "CHECK"),
                     "effect": claim.estimate if claim else None, "interval": list(claim.uncertainty.interval) if claim else None,
                     "significant": bool(claim and (claim.uncertainty.interval[0] > 0 or claim.uncertainty.interval[1] < 0)),
                     "srm_p": res.values.get("srm_p_value") if res and res.status != "REFUSED" else None,
                     "units": {"test": prim[0].get("testUnits"), "control": prim[0].get("controlUnits")},
                     "planned_split": sorted(g.get("size") for g in groups),
                     "note": None if r.state.outcome == "COMPLETED" else (r.state.failures[0].detail[:160] if r.state.failures else None),
                     "crosscheck": None if x is None else {"statsig_delta": x["statsig_delta"], "engine_delta": x["core_delta"],
                                                           "statsig_ci": x["statsig_ci"], "engine_ci": x["core_ci"],
                                                           "level": x["level"], "max_abs_diff": x["max_abs_diff"]},
                     "_ref": (exp, var, slug(test["name"]), slug(control["name"]))})
    return rows


# ── the watcher run ───────────────────────────────────────────────────────────────────────────────────


def run() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "reports").mkdir(exist_ok=True)
    events, actions, board = [], [], []

    def ev(t, exp, kind, detail, **extra):
        events.append({"t": iso(t), "experiment": exp, "kind": kind, "detail": detail, **extra})

    bd = backdrop()
    sandbox = MockStatsigProject({e: {"status": "running", "rollout": {"control": 50, "variant": 50}, "annotations": [],
                                      "decision_frozen": False} for e in ("EXP-10", "EXP-11", "EXP-12")})
    status = {e: "HEALTHY" for e in ("EXP-10", "EXP-11", "EXP-12")}
    headline = {"EXP-10": "Onboarding path (iOS)", "EXP-11": "Paywall copy B", "EXP-12": "Lesson screen layout (web)"}
    sim = simulate()
    stop = {}
    d_tmp = Path(tempfile.mkdtemp(prefix="uc1-run-"))
    ledger = Ledger(d_tmp / "ledger.db"); sink = IdempotentSink(d_tmp / "sink.db")
    ctx = TaskContext(studies={"EXP-10": design("EXP-10", "lesson_completion"), "EXP-11": design("EXP-11", "trial_starts")},
                      monitors={"trial_start_diff_z": MonitorSpec(0.0, 1.0)})
    co = TaskCoordinator(ledger, DemoPort(sim["observations"], "demo-statsig-replay"), sink, context=ctx)
    pending = []                                  # (approve_at, proposal)
    timings = []
    for d in range(1, DAYS + 1):
        for hour, minute in ((4, 0), (14, 15)):
            now = START + timedelta(days=d, hours=hour, minutes=minute)
            ev(now, "*", "wake", f"woke after the {'daily exposure load (03:50 UTC)' if hour == 4 else 'scheduled Pulse reload'}")
            # approvals that arrived since the last wake
            for approve_at, p in list(pending):
                if approve_at <= now:
                    a = Approval.grant(p, APPROVER, approve_at)
                    rec = sandbox.execute(p, a, approve_at + timedelta(minutes=1))
                    actions.append({"proposal": asdict(p), "approval": asdict(a), "receipt": asdict(rec)})
                    ev(approve_at, p.target, "approval", f"approved by {APPROVER}")
                    ev(approve_at + timedelta(minutes=1), p.target, "execution", f"{rec.result} in {rec.sandbox}: {p.kind}")
                    if p.kind == "set_rollout":
                        status[p.target] = "STOPPED"; stop[p.target] = d
                    if p.kind == "annotate_freeze":
                        status[p.target] = "PAUSED"
                    pending.remove((approve_at, p))
            if hour == 4:
                # EXP-10: assignment integrity + effect
                if status["EXP-10"] == "HEALTHY":
                    t0 = time.perf_counter()
                    r = co.run(task(f"uc1-EXP-10-d{d}", {"question_kind": "ASSESS_STUDY", "study_id": "EXP-10",
                                                         "outcomes": ["lesson_completion"]}, now))
                    timings.append((time.perf_counter() - t0) * 1000)
                    res = next((x for m, x in r.state.results if m == "randomized_effect_v1"), None)
                    if res is not None and res.status == "REFUSED" and res.refusal_code == "SRM_FAIL":
                        sp = sim["srm_path"][d - 1]          # day d's data landed at 03:50 today
                        share = sp["missing_id_control"] / max(1, sp["missing_id_control"] + sp["missing_id_variant"])
                        status["EXP-10"] = "INVALID"
                        ev(now, "EXP-10", "incident", f"assignment broken: {res.refusal_reason}", tier="T1",
                           latency_ms=round(r.timings_ms.get("final_published", 0), 1))
                        ev(now, "EXP-10", "diagnosis", f"{share:.0%} of exposures without a user ID are in control "
                           f"({sp['missing_id_control']:,} vs {sp['missing_id_variant']:,}): consistent with users evaluated before "
                           "their ID is set landing in one bucket")
                        p = ActionProposal.make("uc1", sandbox.name, "EXP-10", "annotate_freeze",
                                                {"note": "Results paused: assignment broken (missing user IDs in one arm).",
                                                 "ticket": "App: set user ID before evaluating experiments"},
                                                "assignment integrity not established; any effect estimate would be invalid", None,
                                                "low", [r.reports[-1]["task_id"]], iso(now))
                        ev(now, "EXP-10", "proposal", "freeze decision, annotate, open fix ticket")
                        pending.append((now + timedelta(minutes=18), p))
                        (OUT / "reports" / f"EXP-10-d{d}.json").write_text(json.dumps(r.reports[-1], indent=1))
                    else:
                        ev(now, "EXP-10", "check", "assignment balanced; no change", tier="T1")
                # EXP-11: guardrail monitor (two-sided sequential test on daily z)
                if status["EXP-11"] == "HEALTHY":
                    t0 = time.perf_counter()
                    r = co.run(task(f"uc1-EXP-11-mon-d{d}", {"question_kind": "DETECT_CHANGE", "variables": ["trial_start_diff_z"],
                                                             "baseline_policy": "declared null: no difference"}, now, "monitor"))
                    timings.append((time.perf_counter() - t0) * 1000)
                    mon = next((x for m, x in r.state.results if m == "savi_eprocess_v1"), None)
                    if mon is not None and mon.status != "REFUSED" and mon.values["alarm"]:
                        status["EXP-11"] = "ALARM"
                        ev(now, "EXP-11", "incident", f"guardrail alarm: trial starts {mon.values['direction']} in variant B",
                           tier="T1", latency_ms=round(r.timings_ms.get("final_published", 0), 1))
                        (OUT / "reports" / f"EXP-11-monitor-d{d}.json").write_text(json.dumps(r.reports[-1], indent=1))
                        r2 = co.run(task(f"uc1-EXP-11-eff-d{d}", effect_q("EXP-11", "trial_starts"), now, "monitor"))
                        claim = r2.state.claims[0] if r2.state.claims else None
                        (OUT / "reports" / f"EXP-11-effect-d{d}.json").write_text(json.dumps(r2.reports[-1], indent=1))
                        if claim:
                            lo, hi = claim.uncertainty.interval
                            exp_ = {"metric": "trial starts per day recovered by stopping variant B", "mean": -claim.estimate * N11,
                                    "lower": -hi * N11, "upper": -lo * N11}
                            ev(now, "EXP-11", "diagnosis", f"effect of variant B on trial starts {claim.estimate:+.4f} per user "
                               f"[{lo:+.4f}, {hi:+.4f}] ({claim.assessment_status})")
                            p = ActionProposal.make("uc1", sandbox.name, "EXP-11", "set_rollout",
                                                    {"rollout": {"control": 100, "variant": 0}, "status": "stopped",
                                                     "note": "Variant B stopped: guardrail harm (trial starts)."},
                                                    "variant B lowers trial starts; stopping it ends the loss", exp_, "low",
                                                    [r.reports[-1]["task_id"], r2.reports[-1]["task_id"]], iso(now))
                            ev(now, "EXP-11", "proposal", f"stop variant B (rollout 0%); expected +{exp_['mean']:.0f} trial starts/day "
                               f"[{exp_['lower']:.0f}, {exp_['upper']:.0f}]")
                            pending.append((now + timedelta(minutes=25), p))
                    elif status["EXP-11"] == "HEALTHY":
                        ev(now, "EXP-11", "check", "guardrail within bounds", tier="T1")
                if status["EXP-12"] == "WAITING":
                    full = sim["exp12_daily"][d - 2]
                    trailing = float(np.mean(sim["exp12_daily"][max(0, d - 9):d - 2]))
                    status["EXP-12"] = "HEALTHY"
                    ev(now, "EXP-12", "resolution", f"complete data: {full:,.0f} lesson starts vs {trailing:,.0f} 7-day mean "
                       f"({full / trailing - 1:+.1%}); no action")
            if hour == 14 and d == 9:
                # EXP-12: a same-day dashboard alert arrives; the agent checks data completeness first
                partial = sim["exp12_daily"][d - 1] * (13 + 40 / 60) / 24
                trailing = float(np.mean(sim["exp12_daily"][max(0, d - 8):d - 1]))
                dq = evaluate_dq([BucketQuality(f"day-{d}", (13 + 40 / 60) / 24, True)])
                status["EXP-12"] = "WAITING"
                ev(now, "EXP-12", "incident", f"dashboard alert: lesson starts {partial / trailing - 1:+.0%} today", tier="T0")
                ev(now, "EXP-12", "diagnosis", f"data quality: {dq.values['verdicts'][f'day-{d}']} — today's exposures arrive with the "
                   f"next daily load (03:50 UTC); {(13 + 40 / 60) / 24:.0%} of the day loaded so far", tier="T0")
                ev(now, "EXP-12", "decision", "no action; re-check after the next daily load")
                (OUT / "dq_EXP-12.json").write_text(json.dumps({"day": d, "partial_value": partial, "trailing_mean": trailing,
                                                                "completeness": (13 + 40 / 60) / 24,
                                                                "verdict": dq.values["verdicts"][f"day-{d}"],
                                                                "next_morning_value": sim["exp12_daily"][d - 1]}, indent=1))
            board.append({"t": iso(now), "experiments": [{"id": b["id"], "area": b["area"], "metric": b["metric"], "status": b["status"]}
                                                         for b in bd] +
                          [{"id": e, "area": "Simulated", "metric": headline[e], "status": status[e]} for e in ("EXP-10", "EXP-11", "EXP-12")]})
        if stop and "EXP-11" in stop and not sim.get("_restopped"):
            sim2 = simulate(stop_day={"EXP-11": stop["EXP-11"]})        # the sandboxed world after the action
            sim["daily_trial_starts"] = sim2["daily_trial_starts"]; sim["_restopped"] = True
    # ship request at the end, on a real backdrop experiment with a clear positive effect (if any)
    healthy = [b for b in bd if b["status"] == "HEALTHY" and b["interval"]]
    cand = next((b for b in healthy if b["significant"] and b["effect"] > 0), None) or max(
        healthy, key=lambda b: b["effect"] / max(1e-12, (b["interval"][1] - b["interval"][0]) / 2))
    exp, var, tr, ct = cand["_ref"]
    client = ReadOnlyStatsigClient(cache_dir=CACHE); port = StatsigEvidencePort(client)
    det, _ = client.experiment(exp)
    dd = Path(tempfile.mkdtemp(prefix="uc1-ship-"))
    co_s = TaskCoordinator(Ledger(dd / "l.db"), port, IdempotentSink(dd / "s.db"),
                           context=TaskContext(studies={slug(exp): api.parse_study_design(design_from_statsig(det))}))
    r = co_s.run(SC.effect_task("uc1-ship", exp, var, tr, ct, "2026-11-22T00:00:00Z"), requested_action="ship the variant to 100% of users")
    ship_at = START + timedelta(days=DAYS, hours=15)
    ev(ship_at, cand["id"], "request", "ship the variant to everyone?")
    ev(ship_at, cand["id"], "policy", f"{r.decisions[-1].verdict}: {r.decisions[-1].reasons[0][:120]}")
    ev(ship_at, cand["id"], "proposal", "ship proposal sent to the experiment owner for review; nothing changed")
    # outputs
    mon_z = [x["z"] for x in sim["z_path"]]
    path = log_wealth_path(np.array(mon_z))
    (OUT / "monitor_EXP-11.json").write_text(json.dumps({"z": sim["z_path"], "log_wealth": [float(x) for x in path],
                                                         "threshold": math.log(20), "daily_trial_starts": sim["daily_trial_starts"],
                                                         "stopped_on_day": stop.get("EXP-11")}, indent=1))
    (OUT / "srm_EXP-10.json").write_text(json.dumps(sim["srm_path"], indent=1))
    spread = []                                   # how typical is this run's alarm day? same e-process function, 40 seeds
    for sd in range(1, 41):
        w = log_wealth_path(np.array([x["z"] for x in simulate(seed=sd)["z_path"]]))
        spread.append(next((i + 1 for i, x in enumerate(w) if x >= math.log(20)), None))
    (OUT / "alarm_day_spread_EXP-11.json").write_text(json.dumps({
        "seeds": 40, "alarm_days": spread, "median": float(np.median([x for x in spread if x])),
        "no_alarm_within_window": sum(x is None for x in spread), "this_run_seed": 11,
        "note": "same two-sided regularized e-process as the core's monitor, run on each simulated z path"}, indent=1))
    (OUT / "board.json").write_text(json.dumps(board, indent=1))
    (OUT / "events.jsonl").write_text("\n".join(json.dumps(e) for e in sorted(events, key=lambda e: e["t"])) + "\n")
    (OUT / "actions.json").write_text(json.dumps(actions + [{"refused_example": asdict(sandbox.execute(
        ActionProposal.make("uc1", "production-statsig", "EXP-11", "set_rollout", {"rollout": {"variant": 100}}, "ship", None, "high", [],
                            iso(ship_at)), None, ship_at))}], indent=1))
    (OUT / "crosscheck.json").write_text(json.dumps([{k: v for k, v in b.items() if k != "_ref"} for b in bd], indent=1))
    (OUT / "ship_request.json").write_text(json.dumps({"experiment": cand["id"], "report": r.reports[-1],
                                                       "decision": asdict(r.decisions[-1]) if hasattr(r.decisions[-1], "__dataclass_fields__") else str(r.decisions[-1])}, indent=1, default=str))
    (OUT / "truth.json").write_text(json.dumps(sim["truth"], indent=1))
    (OUT / "timings.json").write_text(json.dumps({"tasks": len(timings), "median_ms": float(np.median(timings)) if timings else None,
                                                  "max_ms": float(max(timings)) if timings else None}, indent=1))
    print(f"wrote {OUT}: {len(events)} events, {len(actions)} executed actions, {len(board)} board states")
    for e in sorted(events, key=lambda e: e["t"]):
        if e["kind"] not in ("wake", "check"):
            print(f"  {e['t'][:16]} {e['experiment']:<7} {e['kind']:<10} {e['detail'][:110]}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
