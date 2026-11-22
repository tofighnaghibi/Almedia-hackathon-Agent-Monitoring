"""Internal-only TabPFN evaluation against the NumPy methods (v3.2 §6.4; user decision 2026-11-22; CP-037).

Runs OUTSIDE the core: TabPFN is never imported by causal_core/. Use the isolated environment:

    ~/.venvs/causal-core-tabpfn/bin/python -m scripts.tabpfn_eval            # from causal_core/
    one-time: accept each model's licence at https://ux.priorlabs.ai and export TABPFN_TOKEN=<your API key>
    TABPFN_MODEL_VERSION=v3.5 forces a version; by default the newest licensed version is used.

Fairness rules (revised after the user's review, 2026-11-22):
  * every method sees the same 200 days (the core's shape set fits on 120 and weighs shapes on 80; the others fit on 200);
  * TabPFN gets effect intervals by bootstrap: refit on resampled days, take the spread of the 130-vs-100 differences
    (its per-row predictive interval mixes daily noise into an effect, which would double-count it);
  * paired errors per world with standard errors, so a gap can be judged real or noise;
  * a realistic world (C) with an interaction, a kink and a categorical input, not only a smooth curve that a
    quadratic fits by construction.
A. covariate baseline prediction (allowed use, §6.4).  B. smooth curved mechanism.  C. interaction + kink + category.
B and C are exploratory (equation shape is outside §6.4's allowed uses). Nothing here feeds a decision path.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np

from causal_core.counterfactual.mechanisms import fit_shape_set
from emulator import dynamics_world as D
from evaluation.fixture_io import PROJECT_ROOT

ORDER = ("v3.5", "v3", "v2.6", "v2.5", "v2")
T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)


def tabpfn_factory():
    """(factory or None, description). Picks the newest licensed version unless TABPFN_MODEL_VERSION is set."""
    try:
        import torch
        from tabpfn import TabPFNRegressor
        from tabpfn.constants import ModelVersion
    except ImportError:
        return None, "tabpfn not installed in this environment (use ~/.venvs/causal-core-tabpfn/bin/python)"
    wanted = [os.environ["TABPFN_MODEL_VERSION"]] if os.environ.get("TABPFN_MODEL_VERSION") else list(ORDER)
    probe_X, probe_y = np.random.default_rng(0).uniform(0, 1, (30, 2)), np.arange(30.0)
    skipped = []
    for v in wanted:
        # measured 2026-11-22 on 200 rows: CPU 0.91 s vs MPS 1.50 s per fit (v3.5); override with TABPFN_DEVICE=mps
        for device in ((os.environ["TABPFN_DEVICE"],) if os.environ.get("TABPFN_DEVICE") else ("cpu",)):
            try:
                make = (lambda v=v, device=device, seed=0:
                        TabPFNRegressor.create_default_for_version(ModelVersion(v), device=device, random_state=seed))
                make().fit(probe_X, probe_y).predict(probe_X[:2])
                return make, f"model {v} on {device}" + (f" (not licensed yet: {', '.join(skipped)})" if skipped else "")
            except Exception as e:  # noqa: BLE001
                if "license" in str(e).lower():
                    skipped.append(v)
                    break
    return None, f"no licensed TabPFN version (tried {', '.join(wanted)})"


def ols(X, y):
    X1 = np.column_stack([np.ones(len(X)), X])
    b = np.linalg.lstsq(X1, y, rcond=None)[0]
    s2 = float(((y - X1 @ b) ** 2).sum() / (len(y) - X1.shape[1]))
    return b, s2 * np.linalg.inv(X1.T @ X1)


def boot_effect(make, X, y, x_hi, x_lo, n_boot, seed):
    """Point effect from the full fit; 90% interval from refits on bootstrap resamples."""
    m = make()
    m.fit(X, y)
    point = float(m.predict(x_hi[None])[0] - m.predict(x_lo[None])[0])
    rng, effs = np.random.default_rng(seed), []
    for b in range(n_boot):
        idx = rng.integers(0, len(y), len(y))
        mb = make(seed=b + 1)
        mb.fit(X[idx], y[idx])
        effs.append(float(mb.predict(x_hi[None])[0] - mb.predict(x_lo[None])[0]))
    lo, hi = np.quantile(effs, [0.05, 0.95])
    return point, float(lo), float(hi)


def shape_set_effect(series, parents, x_hi: dict, x_lo: dict, seed):
    ts = sorted(series["visitors"])
    mset, _ = fit_shape_set(series, "visitors", parents, mechanism_id="m:visitors", version=1,
                            scope=D.scope(), lineage_roots=("eval",), fit_start=ts[0], fit_end=ts[119],
                            val_start=ts[120], val_end=ts[-1])
    rng, effs = np.random.default_rng(seed), []
    for m_, wt in zip(mset.members, mset.weights):
        B = rng.multivariate_normal(m_.coefficients, m_.covariance, int(4000 * wt) + 1)
        f = lambda x: np.array([1.0] + [x[p.variable_id] ** 2 if p.transform == "square" else x[p.variable_id] for p in m_.parents])
        effs.append(B @ (f(x_hi) - f(x_lo)))
    e = np.concatenate(effs)
    lo, hi = np.quantile(e, [0.05, 0.95])
    return float(e.mean()), float(lo), float(hi)


def score(rows, truth_key="truth"):
    out = {}
    for name in rows[0]["methods"]:
        err = np.array([abs(r["methods"][name][0] - r[truth_key]) for r in rows])
        cov = [r["methods"][name][1] <= r[truth_key] <= r["methods"][name][2] for r in rows]
        out[name] = {"mean_abs_error": float(err.mean()), "se": float(err.std(ddof=1) / np.sqrt(len(err))),
                     "interval_covers_truth_at_90": float(np.mean(cov)),
                     "median_interval_width": float(np.median([r["methods"][name][2] - r["methods"][name][1] for r in rows]))}
    names = list(out)
    for a in names:
        for b in names:
            if a < b:
                d = np.array([abs(r["methods"][a][0] - r[truth_key]) - abs(r["methods"][b][0] - r[truth_key]) for r in rows])
                out[f"paired {a} - {b}"] = {"mean_diff": float(d.mean()), "se": float(d.std(ddof=1) / np.sqrt(len(d)))}
    return out


def question_a(make, seeds=range(10)) -> dict:
    """Daily metric = weekday effect + saturating effect of an exogenous driver + noise. Train 120 days, test 60."""
    res = {"linear_calendar": [], "linear_calendar_plus_driver": [], "tabpfn_calendar_plus_driver": []}
    for sd in seeds:
        rng = np.random.default_rng(sd)
        wd = np.arange(180) % 7
        driver = rng.uniform(0, 10, 180)
        y = 100 + np.array([3, 2, 0, -1, -1, -2, -1])[wd] + 12 * (1 - np.exp(-driver / 2)) + rng.normal(0, 1.5, 180)
        cal = np.eye(7)[wd][:, 1:]
        for name, X in (("linear_calendar", cal), ("linear_calendar_plus_driver", np.column_stack([cal, driver]))):
            b, _ = ols(X[:120], y[:120])
            res[name].append(float(np.var(y[120:] - np.column_stack([np.ones(60), X[120:]]) @ b)))
        if make is not None:
            m = make()
            m.fit(np.column_stack([wd, driver])[:120], y[:120])
            res["tabpfn_calendar_plus_driver"].append(float(np.var(y[120:] - m.predict(np.column_stack([wd, driver])[120:]))))
    base = np.mean(res["linear_calendar"])
    return {k: {"residual_variance": float(np.mean(v)), "explained_vs_calendar": float(1 - np.mean(v) / base)} if v else None
            for k, v in res.items()} | {"noise_variance": 2.25}


def question_b(make, seeds=range(200, 230), n_boot=20) -> dict:
    """Smooth curved world (spend → visitors), 200 days each. Effect of spend 100 → 130."""
    rows = []
    for sd in seeds:
        w = D.build(seed=sd, family="saturating")
        ts = sorted(w.series["spend"])
        s = np.array([w.series["spend"][t] for t in ts]); v = np.array([w.series["visitors"][t] for t in ts])
        truth = float(D.visitors_mean([130.0], "saturating")[0] - D.visitors_mean([100.0], "saturating")[0])
        b, C = ols(s[:, None], v)
        se = 30 * float(np.sqrt(C[1, 1]))
        methods = {"straight_line": (30 * b[1], 30 * b[1] - 1.645 * se, 30 * b[1] + 1.645 * se),
                   "core_shape_set": shape_set_effect({"spend": w.series["spend"], "visitors": w.series["visitors"]},
                                                      [("spend", 0)], {"spend": 130.0}, {"spend": 100.0}, sd)}
        if make is not None:
            methods["tabpfn"] = boot_effect(make, s[:, None], v, np.array([130.0]), np.array([100.0]), n_boot, sd)
        rows.append({"truth": truth, "methods": methods})
    return {"worlds": len(rows), "truth": rows[0]["truth"], "scores": score(rows)}


def world_c(seed: int, n: int = 200):
    """visitors = 200 + 8·min(spend,110) + 2·max(spend−110,0) + promo·(150 + 4·spend) + weekday + N(0, 40²)."""
    rng = np.random.default_rng(seed)
    spend, promo, wd = rng.uniform(50, 150, n), (rng.random(n) < 0.3).astype(float), np.arange(n) % 7
    week = np.array([60, 40, 0, -10, -20, -40, -30])
    f = lambda s, p, d: 200 + 8 * np.minimum(s, 110) + 2 * np.maximum(s - 110, 0) + p * (150 + 4 * s) + week[d]
    return spend, promo, wd, f(spend, promo, wd) + rng.normal(0, 40, n), f


def question_c(make, seeds=range(300, 325), n_boot=20) -> dict:
    """Effect of spend 100 → 130 on promo days and on normal days (truth 240 and 120): a kink, an interaction and a
    categorical input. The core's shape set gets the declared parents spend and promo (straight + squared terms)."""
    out = {}
    for promo_val in (0.0, 1.0):
        rows = []
        for sd in seeds:
            spend, promo, wd, y, f = world_c(sd)
            truth = float(f(130.0, promo_val, 0) - f(100.0, promo_val, 0))
            X = np.column_stack([spend, promo])
            b, C = ols(X, y)
            e = 30 * b[1]; se = 30 * float(np.sqrt(C[1, 1]))
            ts = [T0 + timedelta(days=i) for i in range(len(y))]
            series = {"spend": dict(zip(ts, spend.tolist())), "promo": dict(zip(ts, promo.tolist())),
                      "visitors": dict(zip(ts, y.tolist()))}
            methods = {"straight_line": (e, e - 1.645 * se, e + 1.645 * se),
                       "core_shape_set": shape_set_effect(series, [("spend", 0), ("promo", 0)],
                                                          {"spend": 130.0, "promo": promo_val}, {"spend": 100.0, "promo": promo_val}, sd)}
            if make is not None:
                methods["tabpfn"] = boot_effect(make, np.column_stack([spend, promo, wd]), y,
                                                np.array([130.0, promo_val, 3]), np.array([100.0, promo_val, 3]), n_boot, sd)
            rows.append({"truth": truth, "methods": methods})
        out["promo days" if promo_val else "normal days"] = {"worlds": len(rows), "truth": rows[0]["truth"], "scores": score(rows)}
    return out


def show(title, block):
    print(f"\n{title}")
    for k, v in block["scores"].items():
        if k.startswith("paired"):
            sig = "real" if abs(v["mean_diff"]) > 2 * v["se"] else "within noise"
            print(f"    {k:<40} {v['mean_diff']:+7.1f} ± {v['se']:.1f}  ({sig})")
        else:
            print(f"  {k:<16} error {v['mean_abs_error']:6.1f} ± {v['se']:.1f}   90% interval covers truth "
                  f"{v['interval_covers_truth_at_90']:.0%}   median width {v['median_interval_width']:.0f}")


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="A,B,C")
    ap.add_argument("--worlds", type=int, default=25, help="worlds per question for B and C")
    ap.add_argument("--boot", type=int, default=20, help="bootstrap refits per TabPFN effect interval")
    args = ap.parse_args()
    qs = set(args.questions.upper().split(","))
    make, why = tabpfn_factory()
    print(f"TabPFN: {why} · questions {sorted(qs)} · worlds {args.worlds} · bootstrap refits {args.boot}")
    t = time.time()
    results = {"tabpfn": why, "worlds": args.worlds, "bootstrap_refits": args.boot}
    if "A" in qs:
        results["A_covariate_baseline"] = question_a(make)
    if "B" in qs:
        b = question_b(make, seeds=range(200, 200 + args.worlds), n_boot=args.boot)
        results["B_smooth_curve"] = b
        show(f"B. smooth curved world, effect of spend 100 → 130 (truth {b['truth']:.1f}), {b['worlds']} worlds", b)
    if "C" in qs:
        c = question_c(make, seeds=range(300, 300 + args.worlds), n_boot=args.boot)
        results["C_interaction_kink_category"] = c
        for label, block in c.items():
            show(f"C. kink + interaction + weekday, {label} (truth {block['truth']:.0f}), {block['worlds']} worlds", block)
    tag = why.split()[1] if make is not None else "none"
    out = PROJECT_ROOT / "docs" / "tabpfn"
    out.mkdir(parents=True, exist_ok=True)
    name = f"eval_{tag}_{''.join(sorted(qs))}.json"
    (out / name).write_text(json.dumps({**results, "seconds": round(time.time() - t, 1)}, indent=2) + "\n")
    print(f"\nwritten docs/tabpfn/{name} ({time.time() - t:.0f} s)")
    return 0 if make is not None else 3


def _legacy_main() -> int:
    make, why = tabpfn_factory()
    print(f"TabPFN: {why}")
    t = time.time()
    a = question_a(make)
    print(f"\nA. covariate baseline (allowed use): unexplained variance on held-out days (noise floor {a['noise_variance']:.2f})")
    for k in ("linear_calendar", "linear_calendar_plus_driver", "tabpfn_calendar_plus_driver"):
        x = a[k]
        print(f"  {k:<30} " + ("not run" if x is None else f"{x['residual_variance']:6.2f}  ({x['explained_vs_calendar']:+.0%})"))
    b = question_b(make)
    show(f"B. smooth curved world, effect of spend 100 → 130 (truth {b['truth']:.1f}), {b['worlds']} worlds", b)
    c = question_c(make)
    for label, block in c.items():
        show(f"C. kink + interaction + weekday, {label} (truth {block['truth']:.0f}), {block['worlds']} worlds", block)
    out = PROJECT_ROOT / "docs" / "tabpfn"
    out.mkdir(parents=True, exist_ok=True)
    tag = why.split()[1] if make is not None else "none"
    (out / f"eval_{tag}.json").write_text(json.dumps({"tabpfn": why, "A_covariate_baseline": a, "B_smooth_curve": b,
                                               "C_interaction_kink_category": c, "seconds": round(time.time() - t, 1)},
                                              indent=2) + "\n")
    print(f"\nwritten docs/tabpfn/eval_{tag}.json ({time.time() - t:.0f} s)")
    return 0 if make is not None else 3


if __name__ == "__main__":
    sys.exit(main())
