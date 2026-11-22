"""Demo timeline and scoring against the ground truth (L7, Person B).

The timeline injects faults and innocent changes into the live marketplace
while the monitor runs, and adds a new metric halfway. The monitor is never
told what was injected: only the scoring here reads the ground truth.

Scoring, per injected scenario:

- **fault**: found if an alerted incident's root cause is one of the
  expected metrics for that fault, in a segment compatible with the injected
  one. Reported: time to the first alert, whether the root cause was
  confirmed, and whether the trigger event was named.
- **planned promo / holiday**: passes if no alert was raised for an incident
  inside its segment during it (that wasn't explained by a fault).
- **late data**: passes if a data-quality issue was opened and resolved by
  the backfill, and no incident alert was raised for that segment meanwhile.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from reporting.issues import compatible, seg_key

HOUR = pd.Timedelta(hours=1)

NEW_METRIC = {
    "name": "orders_per_search",
    "description": "Orders per search query (how well search turns into purchases)",
    "unit": "rate",
    "calculation": "CAST(SUM(orders) AS DOUBLE) / NULLIF(SUM(searches), 0)",
    "dimensions": ["country", "platform", "category"],
    "higher_is_better": True,
    "business_weight": 0.7,
}

# hours after the live start -> actions
TIMELINE: dict[int, list[tuple[str, Any]]] = {
    3: [("inject", "payment_failure")],
    8: [("inject", "planned_promo")],
    30: [("inject", "late_missing_data")],
    36: [("metric", NEW_METRIC)],
    50: [("inject", "stock_out")],
    76: [("inject", "bad_search_release")],
    100: [("inject", "ad_budget_exhaustion"), ("inject", "holiday")],
}

EXPECTED_ROOTS = {
    "payment_failure": {"payment_success_rate"},
    "bad_search_release": {"search_ctr"},
    "stock_out": {"out_of_stock_rate", "add_to_cart_rate", "avg_active_listings"},
    "ad_budget_exhaustion": {"ad_impressions", "ad_clicks", "ad_spend"},
}


def describe_timeline(timeline: dict[int, list[tuple[str, Any]]] = TIMELINE) -> list[str]:
    out = []
    for h in sorted(timeline):
        for action, arg in timeline[h]:
            out.append(f"+{h:>3}h  {action:6} {arg['name'] if isinstance(arg, dict) else arg}")
    return out


def score(monitor) -> dict[str, Any]:
    """Compare what the monitor reported with what was injected."""
    issues = [i for i in monitor.tracker.issues.values() if i.status != "merged"]
    alerted = [i for i in issues if i.alerts]
    used: set[str] = set()
    rows = []
    for t in monitor.live.truth:
        start, end = pd.Timestamp(t["start_time"]), pd.Timestamp(t["end_time"])
        seg = t["affected_segment"]
        preset = t["preset"]
        row = {"scenario": t["scenario_id"], "preset": preset, "kind": t["kind"], "segment": seg_key(seg),
               "start": t["start_time"]}
        if t["kind"] == "fault":
            want = EXPECTED_ROOTS.get(preset, {t["primary_metric"]})
            def first_alert(i):
                return min(pd.Timestamp(a["time"]) for a in i.alerts)

            hits = [i for i in alerted if i.kind == "incident" and i.root and i.root.get("node") in want
                    and compatible(i.root.get("segment") or {}, seg)
                    and start - HOUR <= first_alert(i) <= end + 6 * HOUR]
            hit = min(hits, key=first_alert) if hits else None
            if hit:
                used.add(hit.id)
                first_alert = min(pd.Timestamp(a["time"]) for a in hit.alerts)
                row.update(result="FOUND", issue=hit.id, root=f"{hit.root['node']} in {seg_key(hit.root['segment'])}",
                           confirmed=hit.root.get("status") == "confirmed",
                           hours_to_alert=round((first_alert - start) / HOUR, 1),
                           trigger_named=bool(t.get("related_event_id")) and t["related_event_id"] in json.dumps(hit.report or {}),
                           resolved=hit.status == "resolved")
            else:
                row.update(result="MISSED")
        elif preset == "late_missing_data":
            dq = [i for i in issues if i.kind == "data_quality" and compatible(i.segment, seg)
                  and start - HOUR <= i.opened_at <= end + 6 * HOUR]
            false = [i for i in alerted if i.kind == "incident" and i.segment == seg
                     and start <= min(pd.Timestamp(a["time"]) for a in i.alerts) <= end + 4 * HOUR]
            row.update(result="PASS" if dq and not false else "FAIL",
                       data_issue=dq[0].id if dq else None, resolved=bool(dq) and dq[0].status == "resolved",
                       false_incidents=[i.id for i in false])
            used |= {i.id for i in dq}
        else:  # planned promo / holiday
            false = [i for i in alerted if i.kind == "incident" and all(i.segment.get(k) == v for k, v in seg.items())
                     and start <= min(pd.Timestamp(a["time"]) for a in i.alerts) <= end and i.id not in used]
            row.update(result="PASS" if not false else "FAIL", false_incidents=[i.id for i in false])
        rows.append(row)
    unexplained = [i for i in alerted if i.kind == "incident" and i.id not in used
                   and not any(i.id in r.get("false_incidents", []) for r in rows)]
    found = [r for r in rows if r["kind"] == "fault"]
    summary = {
        "faults_found": f"{sum(r['result'] == 'FOUND' for r in found)}/{len(found)}",
        "root_causes_confirmed": sum(bool(r.get("confirmed")) for r in found),
        "innocent_passed": f"{sum(r['result'] == 'PASS' for r in rows if r['kind'] == 'innocent')}/"
                           f"{sum(r['kind'] == 'innocent' for r in rows)}",
        "other_alerted_issues": [f"{i.id}: {(i.report or {}).get('title')}" for i in unexplained],
        "alerts_sent": len(monitor.alerts),
        "issues_tracked": len(issues),
        "mean_tick_seconds": round(sum(monitor.timings) / max(1, len(monitor.timings)), 2),
    }
    return {"scenarios": rows, "summary": summary}


def scorecard_text(sc: dict[str, Any]) -> str:
    lines = ["", "=" * 96, "SCORECARD (ground truth vs what the monitor reported)", "=" * 96]
    for r in sc["scenarios"]:
        if r["kind"] == "fault":
            detail = (f"{r['issue']} root={r['root']} {'confirmed' if r['confirmed'] else 'hypothesis'}, "
                      f"alert after {r['hours_to_alert']}h"
                      + (", trigger event named" if r.get("trigger_named") else "")
                      + (", resolved" if r.get("resolved") else ", still open")) if r["result"] == "FOUND" else ""
        elif r["preset"] == "late_missing_data":
            detail = (f"data issue {r.get('data_issue')}{' resolved by backfill' if r.get('resolved') else ''}"
                      + (f"; false incidents {r['false_incidents']}" if r.get("false_incidents") else "; no false incident"))
        else:
            detail = "no incident alerted" if r["result"] == "PASS" else f"false incidents {r['false_incidents']}"
        lines.append(f"{r['result']:6} {r['kind']:8} {r['preset']:22} {r['segment']:32} {detail}")
    s = sc["summary"]
    lines += ["-" * 96,
              f"faults found {s['faults_found']} ({s['root_causes_confirmed']} with a confirmed root cause), "
              f"innocent changes passed {s['innocent_passed']}, {s['alerts_sent']} alerts, "
              f"{s['issues_tracked']} issues tracked, {s['mean_tick_seconds']}s per simulated hour"]
    if s["other_alerted_issues"]:
        lines.append("other alerted issues (not injected; natural variation or side effects):")
        lines += [f"  - {x}" for x in s["other_alerted_issues"]]
    return "\n".join(lines)


def save_run(monitor, out_dir: str | Path, sc: dict[str, Any]) -> Path:
    out = Path(out_dir)
    (out / "reports").mkdir(parents=True, exist_ok=True)
    for iss in monitor.tracker.visible():
        if iss.report:
            (out / "reports" / f"{iss.id}.md").write_text(iss.report["markdown"], encoding="utf-8")
    (out / "issues.json").write_text(json.dumps([i.to_dict() for i in monitor.tracker.visible()], indent=1, default=str),
                                     encoding="utf-8")
    (out / "alerts.jsonl").write_text("\n".join(json.dumps(a) for a in monitor.alerts), encoding="utf-8")
    (out / "feed.jsonl").write_text("\n".join(json.dumps(f.to_dict()) for f in monitor.feed), encoding="utf-8")
    (out / "ground_truth.json").write_text(json.dumps({"scenarios": monitor.live.truth}, indent=1), encoding="utf-8")
    (out / "scorecard.json").write_text(json.dumps(sc, indent=1, default=str), encoding="utf-8")
    (out / "scorecard.txt").write_text(scorecard_text(sc), encoding="utf-8")
    return out
