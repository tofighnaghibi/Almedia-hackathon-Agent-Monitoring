"""P1-2 implementation fidelity F1-F6 (tolerances preregistered in docs/p0/P1_2_SESSION_LAYER_PREREG.md)."""
import json
import pathlib
import sys
from datetime import date

import numpy as np
import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from mkt import pipeline  # noqa: E402
from mkt.config import Config  # noqa: E402
from mkt.sim import chain, twin  # noqa: E402
from mkt.sim.sessions import SessionParams  # noqa: E402
from mkt.stitch import cards  # noqa: E402

p = chain.fit(pipeline.canonical_olist(Config.load()))
sp = SessionParams.from_cards()
rr, tb, ex, dv1 = (cards.load(c) for c in ("TC-RR1", "TC-TB1", "TC-EX1", "TC-DV1"))
_, f, _, _, truth = twin.run(p, date(2018, 1, 1), date(2018, 3, 1), 11, None, session_params=sp)
s, v = truth.sessions_factual
res = {}
b, nb = s.filter(pl.col("kind") == "buying"), s.filter(pl.col("kind") != "buying")
for name, sim, est in (("buying", b["n_views"].mean(), rr["estimate"]["views_mean_buying"]),
                       ("non-buying", nb["n_views"].mean(), rr["estimate"]["views_mean_nonbuying"])):
    rel = abs(sim - est) / est
    res[f"F1 views/session {name}"] = {"sim": sim, "card": est, "rel_diff": rel, "pass": rel <= 0.05}
cs = nb["cart_us"].is_not_null().mean()
res["F2 non-buying cart share"] = {"sim": cs, "card": rr["estimate"]["cart_share_nonbuying"],
                                   "pass": abs(cs - rr["estimate"]["cart_share_nonbuying"]) <= 0.01}
lag = ((b["end_us"] - b["first_view_bought_us"]) / 1e6).to_numpy()
for q, key, tol in ((0.5, "lag_view_median_s", 0.10), (0.9, "lag_view_p90_s", 0.15)):
    sim, est = float(np.quantile(lag, q)), tb["estimate"][key]
    res[f"F3 first-view->buy lag q{q}"] = {"sim": sim, "card": est, "rel_diff": abs(sim - est) / est, "pass": abs(sim - est) / est <= tol}
share = v.group_by("position").len().sort("position")["len"].to_numpy() / v.height
rel = share / share[0]
lo, hi = np.array(ex["ci95"]["rel_ctr_lo"]), np.array(ex["ci95"]["rel_ctr_hi"])
inside = [bool(lo[i] <= rel[i] <= hi[i]) for i in range(1, 10)]
res["F4 relative CTR positions 2-10 inside card CI"] = {"sim": rel.tolist(), "inside": inside, "pass": sum(inside) >= 9}
mob = (s.with_columns(pl.col("start_us").cast(pl.Datetime("us")).dt.strftime("%Y-%m").alias("m"))
        .group_by("m").agg((pl.col("device") == "mobile").mean().alias("sim")).sort("m"))
diffs = {r["m"]: r["sim"] - dv1["estimate"]["monthly_share"][r["m"]]["mobile"] for r in mob.to_dicts() if r["m"] >= "2018-01"}
res["F5 mobile share per month (|diff| <= 0.01)"] = {"diff": diffs, "pass": all(abs(d) <= 0.01 for d in diffs.values())}
one = b["order_idx"].n_unique() == b.height == f.height and set(b["order_idx"].to_list()) == set(f["order_idx"].to_list())
before = bool((b["start_us"] < b["end_us"]).all() & (b["start_us"] <= b["checkout_us"]).all())
res["F6 one buying session per order, starting before it"] = {"pass": bool(one and before), "orders": f.height, "buying_sessions": b.height}
out = pathlib.Path("docs/p0/p1_2_fidelity.json")
out.write_text(json.dumps(res, indent=1, default=float))
for k, r in res.items():
    print(("PASS " if r["pass"] else "FAIL ") + k, {x: (round(y, 4) if isinstance(y, float) else y) for x, y in r.items() if x not in ("pass", "sim", "diff", "inside") or isinstance(y, float)})
print(f"{sum(r['pass'] for r in res.values())}/{len(res)} pass")
