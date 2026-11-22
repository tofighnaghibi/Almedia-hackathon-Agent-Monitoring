"""S10 v2 (preregistered in docs/p0/P1_2_S10_V2_PREREG.md): contract port + standin_core with a local baseline."""
import json
import pathlib
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from causal_core.contracts.common import TimeWindow  # noqa: E402
from causal_core.contracts.tasks import DataCut, ExecutionBudget, ExplainObservation, ReasoningTask  # noqa: E402
from mkt import core_port, pipeline  # noqa: E402
from mkt.config import Config  # noqa: E402
from mkt.sim import chain, twin  # noqa: E402
from mkt.sim.sessions import SessionParams  # noqa: E402
from mkt.standin_core import StandinCore  # noqa: E402
from mkt.engine_types import InterventionSpec  # noqa: E402

U = timezone.utc
p = chain.fit(pipeline.canonical_olist(Config.load()))
sp = SessionParams.from_cards()
W0, W1, WEEK = datetime(2018, 2, 12), datetime(2018, 2, 19), "2018-02-12"
SCEN = {"9a": InterventionSpec(id="9a", target="checkout.failure_rate", scope="", operator="set", value=0.4, window_start=W0,
                               window_end=W1, requested_by="scenario"),
        "9b": InterventionSpec(id="9b", target="demand.multiplier", scope="", operator="scale", value=0.6, window_start=W0,
                               window_end=W1, requested_by="scenario"),
        "control": None}
EXPECT = {"9a": "stage ratio completion_rate", "9b": "upstream of sessions_count"}


def task(port, tid):
    return ReasoningTask(schema_version="2.0", task_id=tid, question=ExplainObservation(
        question_kind="EXPLAIN_OBSERVATION", variables=("orders_count",), observed_pattern="weekly change in orders",
        context_window=TimeWindow(start=datetime(2018, 1, 1, 3, tzinfo=U), end=datetime(2018, 4, 2, 3, tzinfo=U))),
        trigger="explicit_question", source_scope=port.scope, data_cut=DataCut(as_of=datetime(2018, 5, 1, tzinfo=U)), priority=5,
        budget=ExecutionBudget(max_wall_ms=600_000, max_queries=20), created_at=datetime(2018, 5, 1, tzinfo=U))


rows = []
for layer in (True, False):
    for name, spec in SCEN.items():
        for seed in (81, 82, 83, 84, 85):
            _, _, _, eng, _ = twin.run(p, date(2018, 1, 1), date(2018, 4, 2), seed, spec, session_params=sp if layer else None)
            port = core_port.emulator_port(eng, sp if layer else None)
            rev, _ = StandinCore(port).run(task(port, f"s10v2-{name}-{seed}"))
            changed = [f for f in rev.findings if f.finding_id.endswith(":change")]
            stage = [f.statement.split("located at: ")[1] for f in rev.findings if f.finding_id.endswith(":stage")]
            flagged_week = any(WEEK in f.statement for f in changed)
            if name == "control":
                correct = not changed
            else:
                correct = flagged_week and stage == [EXPECT[name]]
            rows.append({"layer": layer, "scenario": name, "seed": seed, "changed": [f.statement for f in changed], "stage": stage,
                         "findings": [(f.claim_kind, f.statement) for f in rev.findings], "correct": correct})
            print(layer, name, seed, "stage", stage, "changed", [f.statement[:60] for f in changed], correct, flush=True)
pathlib.Path("docs/p0/p1_2_s10_v2.json").write_text(json.dumps(rows, indent=1))
for layer in (True, False):
    r = {n: sum(x["correct"] for x in rows if x["layer"] == layer and x["scenario"] == n) for n in SCEN}
    print("with layer" if layer else "without layer", r)
r = {n: sum(x["correct"] for x in rows if x["layer"] and x["scenario"] == n) for n in SCEN}
print("S10 v2:", "PASS" if r["9a"] >= 4 and r["9b"] >= 4 and r["control"] >= 4 else "FAIL", r)
