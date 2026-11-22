"""TabPFN speed vs the core's NumPy methods (internal evaluation, CP-037). Run alone: other load distorts timings.

    ~/.venvs/causal-core-tabpfn/bin/python -m scripts.tabpfn_speed            # from causal_core/

Measures wall time on this machine for: first call in a fresh process (includes loading weights), warm fit+predict
across rows {100, 500, 2,000, 10,000} × features {3, 20} on CPU and, when available, MPS; a bootstrap effect
interval (20 refits, 200 rows); and the core's straight-line fit, shape-set fit+validation and a 14-day what-if
rollout on comparable data. Compared with the blueprint's tier targets (T0 < 2 s, T1 < 10 s, T2 < 60 s per attempt).
"""
from __future__ import annotations

import json
import platform
import statistics
import sys
import time

import numpy as np

from evaluation.fixture_io import PROJECT_ROOT


def timed(fn, repeat=3):
    out = []
    for _ in range(repeat):
        t = time.perf_counter()
        fn()
        out.append(time.perf_counter() - t)
    return statistics.median(out)


def main() -> int:
    from scripts.tabpfn_eval import tabpfn_factory
    t_cold = time.perf_counter()
    make, why = tabpfn_factory()                         # first call: imports torch, loads weights, probes licence
    cold = time.perf_counter() - t_cold
    print(f"TabPFN: {why}; first call in this process (import + weights + probe): {cold:.1f} s")
    if make is None:
        return 3
    import torch
    from tabpfn import TabPFNRegressor
    from tabpfn.constants import ModelVersion
    version = why.split()[1]
    devices = ["cpu"] + (["mps"] if torch.backends.mps.is_available() else [])
    rng = np.random.default_rng(0)
    rows = []
    for device in devices:
        for n in (100, 500, 2_000, 10_000):
            for p in (3, 20):
                X = rng.normal(size=(n, p)); y = np.sin(X[:, 0]) + X[:, 1] ** 2 + rng.normal(0, 0.1, n)
                Xq = rng.normal(size=(100, p))

                def run():
                    m = TabPFNRegressor.create_default_for_version(ModelVersion(version), device=device, random_state=0)
                    m.fit(X, y)
                    m.predict(Xq)
                try:
                    sec = timed(run, repeat=2 if n >= 2_000 else 3)
                except Exception as e:  # noqa: BLE001
                    sec = None
                    print(f"  {device} n={n} p={p}: failed ({type(e).__name__}: {str(e)[:80]})")
                rows.append({"device": device, "rows": n, "features": p, "seconds": sec})
                if sec is not None:
                    print(f"  {device:<4} rows {n:>6,} features {p:>2}: fit + predict 100 rows {sec:7.2f} s")

    # bootstrap effect interval as used in the accuracy evaluation
    from scripts.tabpfn_eval import boot_effect
    s = rng.uniform(50, 150, 200); v = 200 + 8 * s + rng.normal(0, 40, 200)
    best = min((r for r in rows if r["rows"] == 500 and r["features"] == 3 and r["seconds"]), key=lambda r: r["seconds"])
    mk = lambda seed=0: TabPFNRegressor.create_default_for_version(ModelVersion(version), device=best["device"], random_state=seed)
    boot = timed(lambda: boot_effect(mk, s[:, None], v, np.array([130.0]), np.array([100.0]), 20, 0), repeat=1)
    print(f"  bootstrap effect interval (20 refits, 200 rows, {best['device']}): {boot:.1f} s")

    # the core's methods on comparable data
    from datetime import timedelta
    from causal_core.contracts.mechanisms import InterventionSpec
    from causal_core.counterfactual.mechanisms import fit_shape_set
    from causal_core.counterfactual.rollouts import rollout
    from emulator import dynamics_world as D
    from evaluation import phase7_cases as P7
    X1 = np.column_stack([np.ones(200), s])
    ols_s = timed(lambda: np.linalg.lstsq(X1, v, rcond=None), repeat=20)
    w = D.build(seed=3)
    ts = sorted(w.series["spend"])
    shape_s = timed(lambda: fit_shape_set(w.series, "visitors", [("spend", 0)], mechanism_id="m", version=1, scope=D.scope(),
                                          lineage_roots=("x",), fit_start=ts[0], fit_end=ts[119], val_start=ts[120],
                                          val_end=ts[-1]), repeat=5)
    mech = P7.register(w)
    roll_s = timed(lambda: rollout(mech, "trials", P7.history(w), 14, (InterventionSpec(variable_id="spend", kind="set",
                                                                                         value=130.0),),
                                   n_samples=2000, seed=3, target_scope_kind="emulator"), repeat=5)
    print(f"\n  core straight-line fit (200 rows): {ols_s * 1000:.3f} ms")
    print(f"  core shape-set fit + held-out validation (2 shapes, 200 days): {shape_s * 1000:.1f} ms")
    print(f"  core 14-day what-if rollout with intervals (2,000 paired samples, shape sets): {roll_s * 1000:.0f} ms")
    out = {"machine": {"platform": platform.platform(), "python": platform.python_version(), "torch": torch.__version__},
           "tabpfn": why, "cold_start_seconds": cold, "fit_predict": rows, "bootstrap_effect_seconds": boot,
           "core": {"ols_ms": ols_s * 1000, "shape_set_fit_validate_ms": shape_s * 1000, "rollout_ms": roll_s * 1000},
           "tier_targets_seconds": {"T0": 2, "T1": 10, "T2": 60}}
    (PROJECT_ROOT / "docs" / "tabpfn").mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "docs" / "tabpfn" / "speed.json").write_text(json.dumps(out, indent=2) + "\n")
    print("\nwritten docs/tabpfn/speed.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
