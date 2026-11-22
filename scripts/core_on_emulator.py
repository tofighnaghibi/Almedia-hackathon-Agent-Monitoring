"""Step 2: the real causal core (causal_core.TaskCoordinator) reading our emulator through the contract port.

    python -I scripts/core_on_emulator.py          # scenarios 8a / 8b / control, seeds 31-33 (~2 min)

The monitor's baseline (weekday-aware, core's learn_baseline) is learned on an OLDER closed window (1 Jan - 4 Mar) and frozen, as the core
requires; it is never estimated from the weeks being checked. Scoring uses the scenario label, outside the core.
"""
import json
import pathlib
import statistics
import sys
import tempfile
from datetime import date, datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "causal_core"))
from adapters.outputs.sqlite_sinks import IdempotentSink  # noqa: E402
from causal_core import api  # noqa: E402
from causal_core.orchestration.coordinator import TaskCoordinator  # noqa: E402
from causal_core.orchestration.planner import MonitorSpec, TaskContext  # noqa: E402
from causal_core.storage.ledger import Ledger  # noqa: E402
from mkt import core_port, pipeline  # noqa: E402
from mkt.config import Config  # noqa: E402
from mkt.engine_types import InterventionSpec  # noqa: E402
from mkt.sim import chain, twin  # noqa: E402

U = timezone.utc
W0, W1 = datetime(2018, 4, 2), datetime(2018, 4, 9)
p = chain.fit(pipeline.canonical_olist(Config.load()))
# null for weekly orders: learned once on an older closed window, then frozen
from causal_core.analytics.baseline import learn_baseline  # noqa: E402
from causal_core.contracts.capabilities import GetSeries  # noqa: E402
from causal_core.contracts.common import TimeWindow  # noqa: E402


def monitor_for(port, vid: str, trend: bool) -> MonitorSpec:
    """Weekday-aware baseline learned on the run's own OLDER closed window (1 Jan - 4 Mar 2018, before any
    intervention) and frozen; the weeks checked afterwards are never used to learn it."""
    r = port.query(GetSeries(schema_version="2.0", request_id=f"baseline:{vid}", variables=(vid,), scope=port.scope,
                             data_cut=datetime(2018, 6, 1, tzinfo=U), purpose="learn frozen baseline", request="get_series", grain="1d",
                             window=TimeWindow(start=datetime(2018, 1, 1, 2, tzinfo=U), end=datetime(2018, 3, 5, 3, tzinfo=U))))
    pts = [(x.t, x.value) for x in r.observations[0].payload.points if x.value is not None]
    return MonitorSpec(baseline=learn_baseline(pts, trend=trend), alpha=0.05, family_size=2)


SCEN = {"8a_logging_drop": InterventionSpec(id="8a", target="logging.drop_rate", scope="event_type == 'order_created'", operator="set",
                                            value=0.4, window_start=W0, window_end=W1, requested_by="scenario"),
        "8b_demand_drop": InterventionSpec(id="8b", target="demand.multiplier", scope="", operator="scale", value=0.6, window_start=W0,
                                           window_end=W1, requested_by="scenario"),
        "control": None}
rows = []
for name, spec in SCEN.items():
    for seed in (31, 32, 33):
        _, _, _, eng, _ = twin.run(p, date(2018, 1, 1), date(2018, 5, 7), seed, spec)
        port = core_port.emulator_port(eng)
        d = pathlib.Path(tempfile.mkdtemp(prefix="core-emu-"))
        co = TaskCoordinator(Ledger(d / "ledger.db"), port, IdempotentSink(d / "sink.db"), context=TaskContext(monitors={"orders_count": monitor_for(port, "orders_count", trend=True),          # option 1
                                                                                "order_payment_ratio": monitor_for(port, "order_payment_ratio", trend=False)}))  # option 2
        task = api.parse_reasoning_task({"schema_version": "2.0", "task_id": f"{name}-{seed}", "trigger": "scheduled",
                                         "source_scope": port.scope.model_dump(mode="json"), "priority": 5,
                                         "data_cut": {"as_of": "2018-06-01T00:00:00Z"}, "budget": {"max_wall_ms": 60_000},
                                         "created_at": "2018-06-01T00:00:00Z",
                                         "question": {"question_kind": "DETECT_CHANGE", "variables": ["orders_count", "order_payment_ratio"],
                                                      "baseline_policy": "weekday baseline learned on 1 Jan - 4 Mar 2018, frozen"}})
        r = co.run(task)
        st = r.state
        finds = [(f.claim_kind, f.statement[:160]) for f in st.findings] if hasattr(st, "findings") else []
        rows.append({"scenario": name, "seed": seed, "outcome": getattr(st, "outcome", None), "findings": finds,
                     "trace": [(a, b) for a, b, *_ in getattr(st, "trace", [])][-6:]})
        print(name, seed, getattr(st, "outcome", None), flush=True)
        for k, f in finds:
            print("     ", k, f[:150], flush=True)
pathlib.Path("docs/p0/core_on_emulator.json").write_text(json.dumps(rows, indent=1, default=str))
