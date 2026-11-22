"""Phase 6: three end-to-end slices on the same core, scored against hidden truth, a baseline, and a load profile.
Evaluation side only. Each slice = its own source world → its own adapter → the SAME coordinator/planner/contracts.

  S1  ratio metric by segment, reporting delay      EXPLAIN_OBSERVATION / DETECT_CHANGE
  S2  randomized study, no monitor configured       ESTIMATE_EFFECT / ASSESS_STUDY
  S3  sensor states + events, no metric fields      DETECT_CHANGE on a state / ASSESS_HYPOTHESIS on the graph
Each slice records one success, one error and one abstention (plan §H task 2).
"""
from __future__ import annotations

import json
import os
import statistics
import tempfile
import threading
import time
from datetime import timedelta
from pathlib import Path

from pydantic import TypeAdapter

from adapters.outputs.sqlite_sinks import IdempotentSink
from adapters.sources.canonical_port import CanonicalEvidencePort
from adapters.sources.emulator_port import EmulatorEvidencePort, admission_requests
from causal_core import api
from causal_core.contracts.admission import AdmissionRequest
from causal_core.graph.store import GraphStore
from causal_core.graph.views import CausalEvidenceView, TemporalView
from causal_core.orchestration.admission import GraphAdmissionCoordinator
from causal_core.orchestration.coordinator import TaskCoordinator
from causal_core.orchestration.planner import MonitorSpec, TaskContext
from causal_core.storage.graph_log import GraphLog
from causal_core.storage.ledger import Ledger
from emulator import coldchain, experiment_world, marketplace_world
from evaluation import phase5_cases as p5

CUT = "2026-11-22T06:00:00Z"


def _task(tid, question, scope, trigger="explicit_question", **budget):
    raw = {"schema_version": "2.0", "task_id": tid, "trigger": trigger, "source_scope": scope, "priority": 5,
           "data_cut": {"as_of": CUT}, "budget": {"max_wall_ms": 10_000, **budget}, "created_at": CUT, "question": question}
    return api.parse_reasoning_task(raw)


def _stores(tag: str):
    d = Path(tempfile.mkdtemp(prefix=f"p6-{tag}-"))
    return Ledger(d / "ledger.db"), IdempotentSink(d / "sink.db")


# ── S1 ───────────────────────────────────────────────────────────────────


def s1():
    w = marketplace_world.build()
    ledger, sink = _stores("s1")
    port = CanonicalEvidencePort(w.observations, "market-warehouse-adapter", supported=("get_strata", "get_aggregate"))
    ctx = TaskContext(dimensions={"web_conversion": api.parse_dimension_spec(w.dimension)})
    co = TaskCoordinator(ledger, port, sink, context=ctx)
    sc = marketplace_world.scope()
    explain = {"question_kind": "EXPLAIN_OBSERVATION", "variables": ["web_conversion"],
               "observed_pattern": "web conversion fell week over week", "context_window": w.window}
    tasks = {"success": _task("s1-explain", explain, sc),
             "error": _task("s1-no-window", {**explain, "context_window": None}, sc),
             "abstention": _task("s1-monitor", {"question_kind": "DETECT_CHANGE", "variables": ["web_conversion"],
                                                "baseline_policy": "none declared"}, sc, "monitor")}
    return co, tasks, w


def s1_baseline(w) -> dict:
    """A fixed dashboard rule under the same data: alert if yesterday's conversions are < 80% of the trailing 7-day mean."""
    by_day: dict[str, float] = {}
    for o in w.observations:
        day = o["record_key"].split(":")[2]
        by_day[day] = by_day.get(day, 0.0) + o["payload"]["numerator"]
    days = sorted(by_day)
    last, trail = by_day[days[-1]], statistics.mean(by_day[d] for d in days[-8:-1])
    return {"rule": "conversions(yesterday) < 0.8 × mean(previous 7 days)", "yesterday": last, "trailing_mean": trail,
            "change": last / trail - 1, "alert": last < 0.8 * trail,
            "what_it_says": f"conversions {last / trail - 1:+.0%}: 'conversion crashed'"}


# ── S2 ───────────────────────────────────────────────────────────────────


def s2():
    co, sink, ledger, world = p5.harness(monitors=False)          # no monitor exists in this slice at all
    broken_co, *_ = p5.harness(experiment_world.build(treated_share=0.56), monitors=False)
    tasks = {"success": p5.task("s2-itt", p5.Q_ITT), "error": p5.task("s2-srm", p5.Q_ITT),
             "abstention": p5.task("s2-ate", p5.Q_ATE)}
    return {"success": co, "error": broken_co, "abstention": co}, tasks, world


# ── S3 ───────────────────────────────────────────────────────────────────


def s3_graph(world, cut):
    adm = GraphAdmissionCoordinator(GraphStore(), GraphLog(os.path.join(tempfile.mkdtemp(), "graph.db")),
                                    EmulatorEvidencePort(world))
    R = TypeAdapter(AdmissionRequest)
    for raw in admission_requests(world, cut):
        adm.admit(R.validate_json(json.dumps(raw)))
    snap = adm.store.pin()
    return CausalEvidenceView(snap), TemporalView(snap), getattr(snap, "version", 0)


def s3():
    world = coldchain.emit(days=3)
    cut = coldchain.START + timedelta(days=3, hours=12)
    view, temporal, version = s3_graph(world, cut)
    ledger, sink = _stores("s3")
    port = CanonicalEvidencePort(world.observations, "sensor-gateway-adapter", supported=("get_series", "get_event_batch"))
    # the adapter declares what it knows about the sensor: 5-minute samples are autocorrelated (not independent)
    ctx = TaskContext(view=view, temporal_view=temporal, graph_version=version,
                      monitors={"freezer_temp_c": MonitorSpec(-21.0, 0.5, dependence="ar1")})
    co = TaskCoordinator(ledger, port, sink, context=ctx)
    sc = world.definitions["freezer_temp_c"]["scope"]
    hyp = lambda tid, src, dst, text: _task(tid, {"question_kind": "ASSESS_HYPOTHESIS", "hypothesis": {
        "statement": text, "source": src, "target": dst, "observable_implications": [f"{dst} moves after {src}"]}}, sc)
    tasks = {"success": hyp("s3-door", "door_open", "freezer_temp_c", "door openings warm the freezer"),
             "error": _task("s3-monitor", {"question_kind": "DETECT_CHANGE", "variables": ["freezer_temp_c"],
                                           "baseline_policy": "declared null"}, sc, "monitor"),
             "abstention": hyp("s3-dispatch", "door_open", "on_time_dispatch_rate",
                               "door openings directly change the dispatch rate")}
    return co, tasks, world


def run_slices() -> dict:
    out = {}
    co1, t1, w1 = s1()
    out["S1"] = {k: co1.run(t) for k, t in t1.items()}
    cos2, t2, w2 = s2()
    out["S2"] = {k: cos2[k].run(t) for k, t in t2.items()}
    co3, t3, w3 = s3()
    out["S3"] = {k: co3.run(t) for k, t in t3.items()}
    out["_worlds"] = {"S1": w1, "S2": w2, "S3": w3}
    return out


def score(slices: dict) -> list[dict]:
    """Independent acceptance per slice (plan §H table), read against each world's hidden truth."""
    w1, w2 = slices["_worlds"]["S1"], slices["_worlds"]["S2"]
    s1r = slices["S1"]["success"].state
    k = next(r for m, r in s1r.results if m == "kitagawa_v1")
    dq_blocked = next(r for m, r in s1r.results if m == "dq_observation_v1").values["domain_attribution_blocked"]
    s2r = slices["S2"]["success"].state
    claim = s2r.claims[0]
    lo, hi = claim.uncertainty.interval
    s3r = slices["S3"]
    return [
        {"slice": "S1", "check": "drop attributed to mix, not to segment rates (truth: rates unchanged)",
         "met": abs(k.values["mix_component"]) > abs(k.values["rate_component"])},
        {"slice": "S1", "check": f"the partly loaded day ({w1.truth['immature_day']}) is a DQ fault, not a domain change",
         "met": any(w1.truth["immature_day"] in b for b in dq_blocked)},
        {"slice": "S1", "check": "no change test without a declared null",
         "met": slices["S1"]["abstention"].state.outcome == "WAITING_FOR_EVIDENCE"},
        {"slice": "S2", "check": f"ITT CI covers truth {w2.truth['itt']:.3f}", "met": lo <= w2.truth["itt"] <= hi},
        {"slice": "S2", "check": "exposure effect not claimed; no monitor involved",
         "met": not slices["S2"]["abstention"].state.claims and all("savi" not in m for m, _ in s2r.results)},
        {"slice": "S2", "check": "broken randomization refused (no estimate)", "met": not slices["S2"]["error"].state.claims},
        {"slice": "S3", "check": "runs with no metric fields: lagged door→temperature link found in the graph",
         "met": any(a.startswith("TEMPORAL_LAG") for a in s3r["success"].state.results[0][1]["active"])},
        {"slice": "S3", "check": "autocorrelated sensor stream refused, not alarmed on",
         "met": any(f.cls == "METHOD_REFUSAL" for f in s3r["error"].state.failures)},
        {"slice": "S3", "check": "no edge recorded → UNKNOWN, not 'no effect'",
         "met": s3r["abstention"].state.results[0][1]["status"] == "UNKNOWN"},
    ]


# ── load profile (plan §H task 5, reduced; see the report for what is not covered) ─────────────────────


def load_profile(n_tasks: int = 100, workers: int = 8, hung_every: int = 25) -> dict:
    """n_tasks mixed tasks from `workers` threads against ONE ledger (one writer); every `hung_every`-th task hits a
    provider that hangs past its deadline. Latency = submit → final revision committed and published."""
    d = Path(tempfile.mkdtemp(prefix="p6-load-"))
    ledger, sink = Ledger(d / "ledger.db"), IdempotentSink(d / "sink.db")
    world = experiment_world.build()
    ctx = TaskContext(studies={k: api.parse_study_design(v) for k, v in world.designs.items()},
                      monitors={"trial_conversion_daily": MonitorSpec(0.20, 0.006)})
    good = CanonicalEvidencePort(world.observations, "emu-product-port")
    hung = CanonicalEvidencePort(world.observations, "emu-product-port", hang_s=0.6)
    lock = threading.Lock()
    lat: dict[str, list[float]] = {"T0": [], "final": []}
    outcomes: dict[str, int] = {}
    errors: list[str] = []
    qs = [(p5.Q_ITT, "explicit_question"), (p5.Q_STUDY, "study_completion"), (p5.Q_MONITOR, "monitor"), (p5.Q_ATE, "explicit_question")]

    def work(i: int):
        q, trig = qs[i % len(qs)]
        port = hung if i % hung_every == hung_every - 1 else good
        co = TaskCoordinator(ledger, port, sink, context=ctx, worker_id=f"w{i}")
        t = p5.task(f"load-{i:03d}", q, trig, max_wall_ms=300)
        t0 = time.perf_counter()
        try:
            r = co.run(t)
            with lock:
                lat["final"].append((time.perf_counter() - t0) * 1000)
                if "T0_published" in r.timings_ms:
                    lat["T0"].append(r.timings_ms["T0_published"])
                outcomes[r.state.outcome] = outcomes.get(r.state.outcome, 0) + 1
        except Exception as exc:                                     # counted, never hidden
            with lock:
                errors.append(f"{type(exc).__name__}: {exc}")

    start = time.perf_counter()
    threads, pending = [], list(range(n_tasks))
    while pending or threads:
        while pending and len([t for t in threads if t.is_alive()]) < workers:
            th = threading.Thread(target=work, args=(pending.pop(0),))
            th.start()
            threads.append(th)
        threads = [t for t in threads if t.is_alive()]
        time.sleep(0.001)
    wall = time.perf_counter() - start

    def pct(xs, q):
        xs = sorted(xs)
        return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1) if xs else None
    import platform, resource
    return {"tasks": n_tasks, "workers": workers, "hung_provider_tasks": n_tasks // hung_every, "wall_s": round(wall, 2),
            "throughput_tasks_per_s": round(n_tasks / wall, 1),
            "T0_ms": {"n": len(lat["T0"]), "p50": pct(lat["T0"], .5), "p95": pct(lat["T0"], .95), "p99": pct(lat["T0"], .99)},
            "final_ms": {"n": len(lat["final"]), "p50": pct(lat["final"], .5), "p95": pct(lat["final"], .95),
                         "p99": pct(lat["final"], .99)},
            "outcomes": outcomes, "errors": errors[:5], "error_count": len(errors),
            "visible_publications": len(sink.visible()),
            "wal_bytes": os.path.getsize(str(d / "ledger.db") + "-wal") if os.path.exists(str(d / "ledger.db") + "-wal") else 0,
            "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024), 1),
            "machine": {"python": platform.python_version(), "platform": platform.platform(), "cpus": os.cpu_count()},
            "targets": "T0 p95 < 2 s, T1 p95 < 10 s (blueprint §5.3)"}


# ── CP-030: learned, frozen baseline (CURE-style) vs a naive frozen mean/sd ─────────────────────────────


WEEKLY = (1.5, 1.0, 0.2, 0.0, -0.4, -1.2, -1.1)      # weekday pattern in noise-SD units (truth, harness side)


def baseline_study(train_days: int = 84, seed: int = 7) -> dict:
    """A seasonal daily series with a drop on day 112. Learn 'normal' on the first `train_days` days, freeze it, then
    monitor the rest: once with the learned baseline, once with a naive mean/sd from the same training window."""
    from datetime import datetime
    from causal_core.analytics.baseline import learn_baseline
    world = experiment_world.build(seed=seed, days=140, drop_day=112, drop=-0.006, daily_sd=0.006, weekly=WEEKLY)
    series = next(o for o in world.observations if o["variable_id"] == "trial_conversion_daily")
    pts = [(datetime.fromisoformat(p["t"].replace("Z", "+00:00")), p["value"]) for p in series["payload"]["points"]]
    train, monitor = pts[:train_days], pts[train_days:]
    learned = learn_baseline(train)
    vals = [v for _, v in train]
    naive = MonitorSpec(statistics.mean(vals), statistics.stdev(vals))
    out = {"train_days": train_days, "drop_day": world.truth["drop_day"], "baseline": learned}
    for label, spec in (("learned", MonitorSpec(baseline=learned)), ("naive", naive)):
        co, *_ = p5.harness(world)
        co.ctx = TaskContext(monitors={"trial_conversion_daily": spec})
        r = co.run(p5.task(f"cp030-{label}", p5.Q_MONITOR, "monitor"))
        res = next(x for m, x in r.state.results if m in ("baseline_shift_v1", "savi_eprocess_v1"))
        first = res.values.get("first_crossing_index") if res.status != "REFUSED" else None
        day = None if first is None else (train_days + first if label == "learned" else first)
        out[label] = {"result": r, "alarm_day": day, "refusal": res.refusal_code}
    out["learned_alarm_before_drop"] = out["learned"]["alarm_day"] is not None and out["learned"]["alarm_day"] < out["drop_day"]
    out["naive_alarm_before_drop"] = out["naive"]["alarm_day"] is not None and out["naive"]["alarm_day"] < out["drop_day"]
    return out


def baseline_type_one(train_days: int = 56, horizon: int = 120, sims: int = 300, seed: int = 3, tau: float | None = None) -> dict:
    """False-alarm rate of the learned baseline vs plugging in the frozen fit, on seasonal null series (no drop)."""
    import math
    from datetime import datetime, timedelta, timezone
    import numpy as np
    from causal_core.analytics.baseline import DEFAULT_TAU, learn_baseline, mixture_log_wealth
    from causal_core.analytics.sequential import log_wealth_path
    rng = np.random.default_rng(seed)
    t0 = datetime(2026, 1, 5, tzinfo=timezone.utc)
    ts = [t0 + timedelta(days=i) for i in range(train_days + horizon)]
    pattern = np.array([WEEKLY[t.weekday()] for t in ts])
    exact = plug = 0
    for _ in range(sims):
        y = pattern + rng.standard_normal(len(ts))
        b = learn_baseline(list(zip(ts[:train_days], y[:train_days])))
        exact += mixture_log_wealth(b, ts[train_days:], list(y[train_days:]), tau or DEFAULT_TAU).max() >= math.log(20)
        X = np.zeros((horizon, 7)); X[:, 0] = 1
        for i, t in enumerate(ts[train_days:]):
            if t.weekday():
                X[i, t.weekday()] = 1
        z = (y[train_days:] - X @ np.array(b.coefficients)) / b.residual_sd
        plug += log_wealth_path(z).max() >= math.log(20)
    return {"train_days": train_days, "horizon": horizon, "sims": sims, "learned_exact": exact / sims, "plug_in": plug / sims}
