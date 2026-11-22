"""Read-only smoke test of the Statsig adapter against real data. Prints to the terminal; writes nothing to the repo
(the ledger and sink go to a temporary directory).

    python -m scripts.statsig_smoke --cache ../hackathon/.statsig_cache --experiment crm1009_lgs_us_en_spanish_hub
    source ~/.zprofile && python -m scripts.statsig_smoke --live --experiment <id>     # needs STATSIG_CONSOLE_TOKEN
    options: --metric "<metricID or name>" (default: first primary metric), --control/--test "<group name>"
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adapters.outputs.sqlite_sinks import IdempotentSink

from adapters.sources.statsig_port import ReadOnlyStatsigClient, StatsigEvidencePort, design_from_statsig, slug
from causal_core import api
from causal_core.orchestration.coordinator import TaskCoordinator
from causal_core.orchestration.planner import TaskContext
from causal_core.storage.ledger import Ledger
from evaluation import statsig_cases as S


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--cache")
    src.add_argument("--live", action="store_true")
    ap.add_argument("--experiment", required=True)
    ap.add_argument("--metric")
    ap.add_argument("--control")
    ap.add_argument("--test")
    a = ap.parse_args(argv)

    client = ReadOnlyStatsigClient(cache_dir=a.cache) if a.cache else ReadOnlyStatsigClient()
    details, _ = client.experiment(a.experiment)
    groups = [g for g in details.get("groups", []) if not g.get("disabled")]
    contrasts = {a.experiment: (a.control, a.test)} if a.control and a.test else None
    port = StatsigEvidencePort(client, contrasts=contrasts)
    pair = port._contrast(a.experiment, groups)
    if isinstance(pair, str):
        print(f"cannot pick a contrast: {pair}. Pass --control and --test.")
        return 2
    control, test = pair
    design = api.parse_study_design(design_from_statsig(details))
    print(f"experiment {details['id']}  status {details.get('status')}  unit {details.get('idType')}")
    print("  groups: " + ", ".join(f"{g['name']} {g['size']}%{' (isControl)' if g.get('isControl') else ''}" for g in groups))
    print(f"  contrast: {test['name']} vs {control['name']}")

    pulse, retrieved = client.pulse(a.experiment, control["id"], test["id"])
    metrics = pulse.get("primaryMetrics", []) + pulse.get("secondaryMetrics", [])
    m = next((x for x in metrics if a.metric in (None, x["metricID"], x.get("metricName"))), None) if metrics else None
    if m is None:
        print(f"  metric not found; available: {[x.get('metricName') for x in metrics][:10]}")
        return 2
    var = slug(m["metricID"])
    cut = (max(retrieved, datetime.now(timezone.utc)) + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")

    d = Path(tempfile.mkdtemp(prefix="statsig-smoke-"))
    co = TaskCoordinator(Ledger(d / "l.db"), port, IdempotentSink(d / "s.db"),
                         context=TaskContext(studies={slug(a.experiment): design}))
    r = co.run(S.effect_task("smoke", a.experiment, var, slug(test["name"]), slug(control["name"]), cut))
    print(f"\n  metric: {m.get('metricName')}  (Pulse ds {pulse.get('ds')}, retrieved {retrieved:%Y-%m-%d %H:%M} UTC)")
    print(f"  core outcome: {r.state.outcome}")
    for f in r.state.findings:
        print(f"    {f.claim_kind}: {f.statement}")
    for x in r.state.failures:
        print(f"    failure {x.cls}: {x.detail}")
    res = next((x for mid, x in r.state.results if mid == "randomized_effect_v1"), None)
    if res is not None:
        for c in res.assumptions_checked:
            print(f"    checked: {c}")
    obs = [o for resp in r.state.responses for o in resp.observations]
    if obs and res is not None and res.status != "REFUSED":
        x = S.cross_check(port, design, a.experiment, var, slug(test["name"]), slug(control["name"]), obs)
        print(f"\n  cross-check at Statsig's level {x['level']:.4f}:")
        print(f"    Statsig delta {float(x['statsig_delta']):+.6g}  CI [{float(x['statsig_ci'][0]):+.6g}, {float(x['statsig_ci'][1]):+.6g}]")
        print(f"    core    delta {x['core_delta']:+.6g}  CI [{x['core_ci'][0]:+.6g}, {x['core_ci'][1]:+.6g}]")
        print(f"    max abs difference {x['max_abs_diff']:.2e}")
    print(f"\n  network calls: {len(client.calls)} (GET only); nothing written to the repository")
    return 0


if __name__ == "__main__":
    sys.exit(main())
