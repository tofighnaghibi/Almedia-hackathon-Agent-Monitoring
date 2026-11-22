"""Look-alike pair (8a logging drop vs 8b demand drop) through the Contracts port and the contract-based
stand-in core. Supplementary (not a gate): checks the conformed path reproduces the P1-1 reasoning."""
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
from mkt.standin_core import StandinCore  # noqa: E402
from mkt.engine_types import InterventionSpec  # noqa: E402

U = timezone.utc
p = chain.fit(pipeline.canonical_olist(Config.load()))
W0, W1 = datetime(2018, 2, 12), datetime(2018, 2, 19)
WEEK = "2018-02-12"
SCEN = {"8a": InterventionSpec(id="8a", target="logging.drop_rate", scope="event_type == 'order_created'", operator="set", value=0.4,
                               window_start=W0, window_end=W1, requested_by="scenario"),
        "8b": InterventionSpec(id="8b", target="demand.multiplier", scope="", operator="scale", value=0.6, window_start=W0,
                               window_end=W1, requested_by="scenario"), "control": None}
EXPECT = {"8a": "DQ_FAILURE", "8b": "ASSOCIATION", "control": None}
rows = []
for name, spec in SCEN.items():
    for seed in (31, 32, 33, 34, 35):
        _, _, _, eng, _ = twin.run(p, date(2018, 1, 1), date(2018, 4, 2), seed, spec)
        port = core_port.emulator_port(eng)
        task = ReasoningTask(schema_version="2.0", task_id=f"{name}-{seed}", question=ExplainObservation(
            question_kind="EXPLAIN_OBSERVATION", variables=("orders_count",), observed_pattern="weekly change",
            context_window=TimeWindow(start=datetime(2018, 1, 1, 3, tzinfo=U), end=datetime(2018, 4, 2, 3, tzinfo=U))),
            trigger="explicit_question", source_scope=port.scope, data_cut=DataCut(as_of=datetime(2018, 5, 1, tzinfo=U)), priority=5,
            budget=ExecutionBudget(max_wall_ms=60_000), created_at=datetime(2018, 5, 1, tzinfo=U))
        rev, gaps = StandinCore(port).run(task)
        wk = [f for f in rev.findings if f.statement.startswith(WEEK) or f":{WEEK}:" in f.finding_id]
        got = wk[0].claim_kind if wk else None
        other = [f.claim_kind for f in rev.findings if f.claim_kind in ("DQ_FAILURE", "ASSOCIATION") and f not in wk]
        ok = (got == EXPECT[name]) if name != "control" else not any(f.claim_kind in ("DQ_FAILURE", "ASSOCIATION") for f in rev.findings)
        rows.append({"scenario": name, "seed": seed, "verdict_target_week": got, "other_period_verdicts": other, "correct": ok})
        print(name, seed, got, other, ok, flush=True)
pathlib.Path("docs/p0/core_lookalike_score.json").write_text(json.dumps(rows, indent=1))
print({n: sum(r["correct"] for r in rows if r["scenario"] == n) for n in SCEN})
