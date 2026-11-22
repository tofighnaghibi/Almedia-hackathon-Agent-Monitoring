"""Stitch marketplace samples through the core (CP-038).

    python -m scripts.stitch_demo                      # reads ../stitch_samples.zip (marketplace-recon output)
    python -m scripts.stitch_demo --zip PATH --no-color
"""
from __future__ import annotations

import argparse
import collections
import sys
import unittest
from io import StringIO
from pathlib import Path

from adapters.sources.stitch_port import load_stitch
from evaluation import stitch_cases as SC

C = {"b": "\033[1m", "d": "\033[2m", "g": "\033[32m", "y": "\033[33m", "r": "\033[31m", "c": "\033[36m", "x": "\033[0m"}


def paint(t, *s):
    return "".join(C[x] for x in s) + t + C["x"] if C["x"] else t


def section(n, title):
    print("\n" + paint(f"[{n}] {title}", "b", "c"))


def run(zip_path: Path) -> int:
    print(paint("CAUSAL CORE · STITCH MARKETPLACE · marketplace-recon samples through the adapter and the core", "b"))
    L = load_stitch(zip_path)
    c = L.counts

    section(1, "Load: contract checks, explicit fixes, quarantine")
    print(f"  files {c['files']} · definitions {c['definitions']} · observations {c['observations_accepted']} as-is + "
          f"{c['observations_converted_to_utc']} converted to UTC · quarantined {c['observations_quarantined'] + c['definitions_quarantined']}")
    print(f"  evidence records {c['evidence_seen']}: schema_version injected into {c['evidence_schema_version_injected']} "
          f"(each passed every other check) · quarantined {c['evidence_quarantined']}")
    for o in L.observations:
        tz = (o.get("clocks") or {}).get("source_timezone") or ""
        if tz.startswith("UTC+") or tz.startswith("UTC-"):
            print(paint(f"    converted {o['variable_id']}: original offset {tz[3:]} kept in clocks.source_timezone", "d"))
    grid = collections.Counter((m["layer"], m["kind"]) for m in L.meta.values())
    kinds = ("metric", "state", "event", "categorical")
    print(paint("  layer   " + "".join(f"{k:>12}" for k in kinds), "d"))
    for layer in sorted({l for l, _ in grid}):
        print(f"  {layer:<6}  " + "".join(f"{grid.get((layer, k), 0) or '·':>12}" for k in kinds))
    envs = collections.Counter((m["environment_kind"], m["environment"]) for m in L.meta.values())
    print(paint("  environments: " + ", ".join(f"{e} ({k}) {n}" for (k, e), n in envs.most_common()), "d"))

    section(2, "Exact ratio identities: ratio = numerator / denominator in every shared window")
    for r in SC.identities(L):
        col = "g" if r["verdict"] == "VERIFIED_IDENTITY" else "y"
        res = "" if r["max_abs_residual"] is None else f"max residual {r['max_abs_residual']:.1e}"
        print(f"  {r['ratio']:<30} = {r['numerator']} / {r['denominator']:<24} {paint(r['verdict'], col):<28} "
              f"{r['windows']} windows {res}")
    print(paint("  UNVERIFIED rows: the ratio and its parts live in different environments (no shared windows), so nothing\n"
                "  can be checked; the core does not assume the identity holds", "d"))

    section(3, "Service telemetry: null frozen on the first half, the second half monitored")
    for v in ("frontend.latency_p95_ms", "frontend.error_rate", "checkout.latency_p95_ms", "checkout.error_rate",
              "payment.latency_p95_ms", "payment.error_rate", "cart.error_rate", "product-catalog.latency_p95_ms"):
        m = SC.telemetry_monitor(L, v)
        verdict = (paint(f"refused: {m['refusal']}", "y") if m["status"] == "REFUSED" else
                   paint("ALARM " + str(m["direction"]), "r") if m["alarm"] else paint("no alarm (failure to reject)", "g"))
        print(f"  {v:<32} {m['points']:>4} pts · lag-1 r {m['lag1_autocorrelation']:+.2f} ({m['dependence']}) · {verdict}")

    section(4, "The Hillstrom A/B sample through the planner")
    co = SC.coordinator(L)
    r = co.run(SC.hillstrom_task(L))
    print(f"  no registered design       → {paint(r.state.outcome, 'y')}: {r.state.failures[0].detail[:90]}")
    co2 = SC.coordinator(L, studies={"hillstrom-email-2008": SC.hillstrom_design(L)})
    for tr in ("Womens_E-Mail", "Mens_E-Mail"):
        r = co2.run(SC.hillstrom_task(L, treatment=tr, tid=f"h-{tr}"))
        head = r.state.findings[0].statement if r.state.findings else "-"
        print(f"  {tr} vs No_E-Mail (design declared) → {paint(r.state.outcome, 'g' if r.state.outcome == 'COMPLETED' else 'y')}: {head[:110]}")
    print(paint("  a 300-user conformance sample: the full 64,000-user dataset is needed for a useful estimate", "d"))

    section(5, "Gates (tests/test_stitch_adapter.py)")
    from tests import test_stitch_adapter as T
    ok = True
    for cls, label in (("StitchAdapterCP038", "version injected only when compliant; offsets converted; rest quarantined"),
                       ("RealStitchSamples", "real samples: 106 definitions, 340 observations, identities verify")):
        res = unittest.TextTestRunner(stream=StringIO(), verbosity=0).run(unittest.defaultTestLoader.loadTestsFromTestCase(getattr(T, cls)))
        ok &= res.wasSuccessful()
        print(f"  {paint('✓', 'g') if res.wasSuccessful() else paint('✗', 'r')} {label:<76}{res.testsRun} test{'s' if res.testsRun != 1 else ''}")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zip", type=Path, default=SC.DEFAULT_ZIP)
    ap.add_argument("--no-color", action="store_true")
    a = ap.parse_args(argv)
    if a.no_color or not sys.stdout.isatty():
        for k in C:
            C[k] = ""
    if not a.zip.exists():
        print(f"no samples at {a.zip}")
        return 2
    return run(a.zip)


if __name__ == "__main__":
    sys.exit(main())
