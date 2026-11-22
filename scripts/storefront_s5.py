"""M4 + M5: storefront projection and scenario #5 (payment failure) through the real Astronomy Shop.

Day A (20 Feb 2018 sim): all buying sessions played through the shop, no fault.
Day B (21 Feb 2018 sim): paymentFailure = 25% (gateway -> flag adapter), same driver.
Checks: commit/release, correlation rows, duplicate-injection idempotency, telemetry signature, leak scan.
"""
import json
import pathlib
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from causal_core.contracts.capabilities import GetAggregate  # noqa: E402
from mkt import pipeline  # noqa: E402
from mkt.adapters.astronomy import storefront as sf  # noqa: E402
from mkt.adapters.astronomy.telemetry import TelemetryPort, WindowMap, load_spans  # noqa: E402
from mkt.config import Config  # noqa: E402
from mkt.emulator.api import Emulator, EmulatorConfig  # noqa: E402
from mkt.sim import chain  # noqa: E402
from mkt.engine_types import InterventionSpec  # noqa: E402

U = timezone.utc
HOME = pathlib.Path.home()
SEED = 21
run = HOME / "data/mkt/runs/storefront-s5"
shutil.rmtree(run, ignore_errors=True)
cfg = Config.load()
canon = pipeline.canonical_olist(cfg)
p = chain.fit(canon)
cat = sf.build_catalog(canon, HOME / "data/mkt/raw", n=200)
print("catalog rows synced:", sf.catalog_sync(cat))
people = json.load(open(HOME / ".cache/otel-demo-3.1.0/src/load-generator/people.json"))
driver = sf.SessionDriver(cat, run / "privileged/driver_results.jsonl", people[0], SEED)
flags = sf.FlagAdapter(HOME / "data/mkt/astronomy/flags/demo.flagd.json")
assert flags.set_variant("paymentFailure", "off")

walls = []


def timed(plans):
    t0 = datetime.now(U)
    out = driver(plans)
    walls.append((t0, datetime.now(U)))
    return out


emu = Emulator(p, run)
emu.initialize(EmulatorConfig(start=date(2018, 1, 1), horizon_end=date(2018, 3, 15)), seed=SEED)
emu.step(date(2018, 2, 20))
emu.start_storefront(datetime(2018, 2, 20), datetime(2018, 2, 21))
emu.step(date(2018, 2, 21), storefront=timed)                                 # day A, no fault
ev = emu.intervene(InterventionSpec(id="s5", target="storefront.payment_failure", scope="", operator="set", value=0.25,
                                    window_start=datetime(2018, 2, 21), window_end=datetime(2018, 2, 22), requested_by="scenario"))
assert ev.status == "accepted", ev.reason
flag = emu.operational_flags()[0]
assert flags.set_variant(flag["flag"], flag["variant"]), "flag read-back failed"
emu.start_storefront(datetime(2018, 2, 21), datetime(2018, 2, 22))
emu.step(date(2018, 2, 22), storefront=timed)                                 # day B, 25% payment failure
assert flags.set_variant("paymentFailure", "off")
emu._sf_window = None
emu.step(date(2018, 3, 1))

cmd = pl.DataFrame(emu.commands).drop("latency_s", strict=False)
cmd = cmd.with_columns(pl.col("purchased_at").str.slice(0, 10).alias("day"))
by_day = cmd.group_by("day", "outcome").len().sort("day", "outcome")
world = emu._world()
ok_idx = set(cmd.filter(pl.col("outcome") == "ok")["order_idx"].to_list())
bad_idx = set(cmd.filter(pl.col("outcome") != "ok")["order_idx"].to_list())
in_ledger = set(world["order_idx"].to_list())
checks = {
    "every ok command is a committed ledger order": ok_idx <= in_ledger,
    "no failed command is in the ledger": not (bad_idx & in_ledger),
    "storefront order ids unique": cmd.filter(pl.col("outcome") == "ok")["storefront_order_id"].n_unique() == len(ok_idx),
}
before = driver.http_checkouts
replay = driver([{k: r[k] for k in ("command_id", "order_idx", "session_alias")} for r in cmd.to_dicts()])   # duplicate injection
checks["duplicate injection: zero new checkouts"] = driver.http_checkouts == before
checks["duplicate injection: identical results"] = all(replay[r["command_id"]]["outcome"] == r["outcome"] for r in cmd.to_dicts())

# telemetry (redacted export), mapped to simulated time per window
import time; time.sleep(8)                                                    # collector batch flush
spans = load_spans(HOME / "data/mkt/astronomy/live/spans.jsonl")
wins = [WindowMap(walls[0][0], walls[0][1], datetime(2018, 2, 20, tzinfo=U), datetime(2018, 2, 21, tzinfo=U)),
        WindowMap(walls[1][0], walls[1][1], datetime(2018, 2, 21, tzinfo=U), datetime(2018, 2, 22, tzinfo=U))]
tp = TelemetryPort(spans, wins, SEED, generator_version="storefront-3.1.0+" + emu.run_id)
from causal_core.contracts.common import TimeWindow  # noqa: E402
rates = {}
for name, (a, b) in {"A": (datetime(2018, 2, 20, tzinfo=U), datetime(2018, 2, 21, tzinfo=U)),
                     "B": (datetime(2018, 2, 21, tzinfo=U), datetime(2018, 2, 22, tzinfo=U))}.items():
    for svc in ("payment", "checkout"):
        r = tp.query(GetAggregate(schema_version="2.0", request_id=f"{name}{svc}", variables=(f"{svc}.error_rate",), scope=tp.scope,
                                  data_cut=datetime(2018, 2, 22, tzinfo=U), window=TimeWindow(start=a, end=b), purpose="s5", request="get_aggregate"))
        o = r.observations[0].payload if r.status == "OK" else None
        rates[f"{name}:{svc}"] = (o.numerator / o.denominator if o and o.denominator else None, o.denominator if o else 0)
checks["telemetry: payment error rate rises in the fault day"] = (rates["B:payment"][0] or 0) > (rates["A:payment"][0] or 0) + 0.1
checks["telemetry: role SANDBOX_OBSERVED"] = tp.evidence_record(r.observations[0]).provenance_role == "SANDBOX_OBSERVED"
scan = json.loads(subprocess.run([sys.executable, "-I", "scripts/m0_e3_scan.py", str(HOME / "data/mkt/astronomy/live/spans.jsonl"),
                                  str(HOME / "data/mkt/astronomy/live/logs.jsonl")], capture_output=True, text=True).stdout)
checks["leak scan (hidden faults): 0 hits"] = scan["total_hits"] == 0

orders_day = world.with_columns(pl.col("purchased_at").dt.date().cast(pl.Utf8).alias("d")).group_by("d").len()
od = {r["d"]: r["len"] for r in orders_day.to_dicts()}
report = {"intervention": ev.status, "flag": flag, "commands_by_day_outcome": by_day.to_dicts(),
          "ledger_orders": {d: od.get(d) for d in ("2018-02-19", "2018-02-20", "2018-02-21", "2018-02-22")},
          "telemetry_error_rates": {k: v[0] for k, v in rates.items()}, "telemetry_requests": {k: v[1] for k, v in rates.items()},
          "wall_seconds": [round((b - a).total_seconds(), 1) for a, b in walls], "leak_hits": scan["total_hits"],
          "checks": checks, "all_pass": all(checks.values())}
pathlib.Path("docs/experiments/storefront_s5.json").write_text(json.dumps(report, indent=1, default=str))
print(json.dumps(report, indent=1, default=str))
