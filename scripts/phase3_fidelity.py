"""Phase 3, gate S5: fit the chain on 2017, simulate Jan-Jun 2018, compare with held-out real data
against the tolerances preregistered in docs/p0/PHASE3_TOLERANCES.md."""
import dataclasses
import json
import pathlib
import sys
import time
from datetime import date, datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from mkt import pipeline  # noqa: E402
from mkt.config import Config  # noqa: E402
from mkt.sim import chain  # noqa: E402

attempt = sys.argv[1] if len(sys.argv) > 1 else "0"
cfg = Config.load()
canon = pipeline.canonical_olist(cfg)
t = time.time()
p = chain.fit(canon)
sim = chain.simulate(p, date(2018, 1, 1), date(2018, 7, 1), seed=1)
w = (datetime(2018, 1, 1), datetime(2018, 7, 1))
real_s, sim_s = chain.stats(chain.real_frame(canon), *w), chain.stats(sim, *w)
res = chain.compare(real_s, sim_s)
out = pathlib.Path("docs/p0") / f"phase3_fidelity_attempt{attempt}.json"
out.write_text(json.dumps({"params_trained_through": str(p.trained_through), "trend": [p.level_a, p.level_b],
                           "simulated_orders": sim.height, "seconds": round(time.time() - t, 1), "results": res,
                           "params": dataclasses.asdict(p) | {"seller_cum": "omitted", "sellers": "omitted", "seller_region": "omitted", "seller_mu": "omitted"}},
                          indent=1, default=str))
for r in res:
    def fmt(v):
        if isinstance(v, dict): return {k: round(x, 3) for k, x in v.items()}
        if isinstance(v, list): return [round(x, 3) for x in v][:7]
        return round(v, 3)
    print(f"{r['id']:4} {'PASS' if r['pass'] else 'FAIL'}  {r['statistic']}\n       real {fmt(r['real'])}\n       sim  {fmt(r['sim'])}")
print("S5:", "PASS" if all(r["pass"] for r in res) else "FAIL", f"({sum(r['pass'] for r in res)}/10)  -> {out}")
