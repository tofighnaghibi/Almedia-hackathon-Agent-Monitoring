"""M3 minimum viable slice, headless closed loop (ASTRONOMY_SHOP_INTEGRATION.md §H, scenario #8).

1 initialize (Olist-calibrated chain, seed)       6 core reads the port (Contracts only)
2 step 6 weeks                                      7 core publishes a gap -> policy grants a measurement
3 hidden coin flip: 8a logging drop / 8b demand drop 8 emulator exposes the stream; step 1 more day
4 step 3 more weeks                                 9 core re-runs (revision 2)
5 seal the answer (privileged)                     10 evaluator opens the seal and scores
"""
from __future__ import annotations

import json
import pathlib
from datetime import date, datetime, timedelta, timezone

from causal_core.contracts.common import TimeWindow
from causal_core.contracts.tasks import DataCut, ExecutionBudget, ExplainObservation, ReasoningTask

from ..emulator.api import Emulator, EmulatorConfig
from ..sim.chain import uniform
from ..standin_core import StandinCore
from ..engine_types import InterventionSpec
from .loop import FileSink, MeasurementPolicy, open_seal, seal

U = timezone.utc
W0, W1 = datetime(2018, 2, 12), datetime(2018, 2, 19)


def _task(port, tid, cut: date) -> ReasoningTask:
    end = datetime(cut.year, cut.month, cut.day, 3, tzinfo=U)
    return ReasoningTask(schema_version="2.0", task_id=tid, question=ExplainObservation(
        question_kind="EXPLAIN_OBSERVATION", variables=("orders_count",), observed_pattern="weekly change in orders",
        context_window=TimeWindow(start=datetime(2018, 1, 1, 3, tzinfo=U), end=end)),
        trigger="scheduled", source_scope=port.scope, data_cut=DataCut(as_of=end), priority=5,
        budget=ExecutionBudget(max_wall_ms=60_000), created_at=end)


def run(params, run_dir: pathlib.Path, seed: int = 7, force: str | None = None) -> dict:
    run_dir = pathlib.Path(run_dir)
    emu = Emulator(params, run_dir)
    emu.initialize(EmulatorConfig(start=date(2018, 1, 1), horizon_end=date(2018, 4, 1),
                                  hidden_variables=("payments_count", "payment_approved")), seed=seed)
    emu.step(date(2018, 2, 12))
    flip = force or ("8a" if uniform(seed, "mvs:coin", __import__("numpy").array([0]))[0] < 0.5 else "8b")
    spec = (InterventionSpec(id="8a", target="logging.drop_rate", scope="event_type == 'order_created'", operator="set", value=0.4,
                             window_start=W0, window_end=W1, requested_by="scenario") if flip == "8a" else
            InterventionSpec(id="8b", target="demand.multiplier", scope="", operator="scale", value=0.6, window_start=W0,
                             window_end=W1, requested_by="scenario"))
    ev = emu.intervene(spec)
    digest = seal(emu.priv, {"scenario": flip, "week": "2018-02-12",
                             "expected": "DQ_FAILURE" if flip == "8a" else "ASSOCIATION", "layer": "observation" if flip == "8a" else "world"})
    emu.step(date(2018, 3, 5))

    sink, policy = FileSink(run_dir / "public" / "sink.jsonl"), MeasurementPolicy({"orders_count": ("payments_count",)})
    port = emu.observe()
    rev1, gaps = StandinCore(port).run(_task(port, "mvs-orders", emu.clock), revision=1)
    sink.publish(rev1); sink.publish_gaps(gaps)
    granted = [g for gap in gaps for g in policy.decide(gap, emu)]
    emu.step(emu.clock + timedelta(days=1))
    port = emu.observe()
    rev2, _ = StandinCore(port).run(_task(port, "mvs-orders", emu.clock), revision=2)
    sink.publish(rev2)

    ans = open_seal(emu.priv, digest)                        # evaluator only, after the core has finished
    target = [f for f in rev2.findings if f":{ans['week']}:" in f.finding_id]
    verdict = target[0].claim_kind if target else None
    report = {"seed": seed, "scenario_hidden": ans["scenario"], "intervention_status": ev.status,
              "rev1_outcome": rev1.outcome, "rev1_gaps": [g.recommended_measurements for g in gaps], "granted": granted,
              "rev2_outcome": rev2.outcome, "verdict_target_week": verdict, "expected": ans["expected"],
              "correct": verdict == ans["expected"], "abstained": verdict in (None, "INCONCLUSIVE"),
              "requested_discriminating_measurement": bool(granted), "ledger_hash": emu.ledger_hash(),
              "rev2_findings": [(f.claim_kind, f.statement) for f in rev2.findings]}
    (run_dir / "report.json").write_text(json.dumps(report, indent=1))
    return report
