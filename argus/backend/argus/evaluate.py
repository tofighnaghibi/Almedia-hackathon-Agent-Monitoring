"""Evaluation harness: seeds × injected faults → detection latency, explained share, fixes, false alarms.

Usage (from backend/):  python -m argus.evaluate                 # seeds 42, 1..8
                        python -m argus.evaluate --seeds 42 7 --clean-days 3 --json

The harness is the evaluator, so it knows when it injected each fault (Argus does not).
"""
from __future__ import annotations

import argparse
import json
import os

DEFAULT_SEEDS = [42, 1, 2, 3, 4, 5, 6, 7, 8]


def evaluate_seed(seed: int, clean_days: int = 7) -> dict:
    from .world import World

    # 1. false alarms: a clean world for `clean_days`
    w = World(seed=seed)
    w.step(clean_days * 24)
    false_incidents = [i["country"] for i in w.agent.incidents]

    # 2. the demo faults on a fresh world (default farm, 10 h ramp)
    w = World(seed=seed)
    w.step(2)
    w.inject("promo", "DE")
    w.step(6)
    promo_incidents = [i["country"] for i in w.agent.incidents if i["country"] == "DE"]
    promo_explained = any(e["country"] == "DE" for e in w.agent.explained)

    start = w.reality.t
    w.inject("bot_farm", "CA", "Android")
    inc = w._step_until_incident("CA", 12)
    row: dict = {"seed": seed, "false_incidents_clean": false_incidents, "promo_incident": bool(promo_incidents),
                 "promo_explained": promo_explained}
    if inc is None:
        row["bot_farm"] = {"detected": False}
        return row
    healthy_after = None
    w.agent.approve(inc["id"], inc["recommended"])
    for h in range(1, 26):
        w.step(1)
        if healthy_after is None and w.agent.country_status(inc["c"]) == "ok":
            healthy_after = h
    top = inc["hypotheses"][0]
    row["bot_farm"] = {"detected": True, "latency_h": inc["detected_at"] - start + 1, "root": inc["root"],
                       "l3_run": inc["l3"] is not None, "explained": top["explained"], "verdict": top["verdict"],
                       "recommended": inc["recommended"], "severity": inc["severity"],
                       "fix_delta_p50_usd": {f["id"]: f["delta_p50_usd"] for f in inc["fixes"]},
                       "verification": inc["verification"]["status"], "healthy_after_h": healthy_after}
    start = w.reality.t
    w.inject("tracking_break", "GB", "iOS")
    inc2 = w._step_until_incident("GB", 12)
    row["tracking_break"] = ({"detected": False} if inc2 is None else
                             {"detected": True, "latency_h": inc2["detected_at"] - start + 1, "root": inc2["root"],
                              "explained": inc2["hypotheses"][0]["explained"], "recommended": inc2["recommended"]})
    row["stray_incidents"] = [i["country"] for i in w.agent.incidents if i["country"] not in ("CA", "GB")]
    return row


def summarise(rows: list[dict]) -> dict:
    bf = [r["bot_farm"] for r in rows if r["bot_farm"]["detected"]]
    tb = [r["tracking_break"] for r in rows if r.get("tracking_break", {}).get("detected")]
    return {
        "seeds": len(rows),
        "false_incidents_clean_weeks": sum(len(r["false_incidents_clean"]) for r in rows),
        "stray_incidents_during_demo": sum(len(r.get("stray_incidents", [])) for r in rows),
        "bot_farm_detected": f"{len(bf)}/{len(rows)}",
        "bot_farm_latency_h": [min(b["latency_h"] for b in bf), max(b["latency_h"] for b in bf)] if bf else None,
        "bot_farm_explained": [min(b["explained"] for b in bf), max(b["explained"] for b in bf)] if bf else None,
        "predictions_verified": sum(b["verification"] == "verified" for b in bf),
        "tracking_detected": f"{len(tb)}/{len(rows)}",
        "tracking_latency_h": [min(b["latency_h"] for b in tb), max(b["latency_h"] for b in tb)] if tb else None,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seeds", type=int, nargs="*", default=DEFAULT_SEEDS)
    ap.add_argument("--clean-days", type=int, default=7)
    ap.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = ap.parse_args(argv)
    os.environ.setdefault("ARGUS_NO_DOTENV", "1")      # evaluation never calls the LLM
    rows = [evaluate_seed(s, args.clean_days) for s in args.seeds]
    out = {"rows": rows, "summary": summarise(rows)}
    if args.json:
        print(json.dumps(out, indent=1))
        return 0
    print(f"{'seed':>4} | false | bot: latency explained verdict    fix                  verified healthy | track: latency")
    for r in rows:
        b, t = r["bot_farm"], r.get("tracking_break", {})
        if not b["detected"]:
            print(f"{r['seed']:>4} | {len(r['false_incidents_clean']):>5} | NOT DETECTED")
            continue
        print(f"{r['seed']:>4} | {len(r['false_incidents_clean']):>5} | {b['latency_h']:>8} h {b['explained']:>8.1%} "
              f"{b['verdict']:<10} {b['recommended']:<20} {b['verification']:<8} {str(b['healthy_after_h']) + ' h':>7} | "
              f"{str(t.get('latency_h', '-')) + ' h':>8}")
    print("summary:", json.dumps(out["summary"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
