"""Stress cases: small targeted databases with one event each, replayed by the brain and judged from its output files.

    python scripts/stress_cases.py                              every case; writes docs/stress.md and docs/stress.json
    python scripts/stress_cases.py --cases traffic_drop,mix_shift --out-dir data/stress-out

The published evaluation (docs/results.md) injects large faults, each with its own release at the right hour and place,
and innocent events that are always announced. These cases measure what it does not: a traffic drop with unchanged rates,
a small payment fault, a fault during a promo, an unannounced mix shift, the recommendations faults, a fault whose release
is missing or late, a fault in two sibling places, a fault that comes back, and a fault while a source is late.

Each case builds a seeded database (DAYS days of history with the routine decoy releases), records its event(s) with the
simulator's own inject functions, runs `python -m brain.run --replay` over the hours around the event, scores the
incident files with sim.score, and checks the expectation stated with the case. The harness judges only from what the
brain wrote (the incident and record files); it never imports brain/. A case that fails is reported as failed: the page
says what the system does today, not what it should do.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim import db, inject  # noqa: E402
from sim import score as sc  # noqa: E402
from sim.run import DEFAULT_START  # noqa: E402
from sim.schema import TS_FORMAT  # noqa: E402

DAYS = 30
EVENT_DAY = 27                     # the event starts on this day of the history, at EVENT_HOUR UTC
EVENT_HOUR = 8
LEAD_HOURS = 3                     # the replay starts this long before the first event
TAIL_HOURS = 12                    # and ends this long after the last one (or at the end of the data)
COUNTED = ("open", "ongoing", "resolved")
HOUR = pd.Timedelta(hours=1)


def _fmt(ts: pd.Timestamp) -> str:
    return ts.strftime(TS_FORMAT)


@dataclass
class Case:
    """One stress case: what is injected (build), and what the system is expected to report (checks)."""
    name: str
    injected: str                                   # in words, for the page
    expect: str                                     # the stated expectation, in words
    build: Callable[[object, pd.Timestamp], None]   # records the events (replay_now=False); t0 = first event hour
    checks: list[str] = field(default_factory=list)  # names in CHECKS, all must pass
    max_incidents: int | None = None                # counted incidents allowed in the whole replay
    seed: int = 11


# ----------------------------------------------------------------------------- reading what the brain wrote
def read_outputs(folder: Path) -> list[dict]:
    """Every incident or record file the brain wrote (top level and notes/), leniently: a file that is not a JSON
    object with an id and a first_seen is skipped. An empty or missing folder is a valid outcome (nothing reported)."""
    out, seen = [], set()
    if not folder.is_dir():
        return out
    for p in sorted(folder.glob("*.json")) + sorted((folder / "notes").glob("*.json")):
        if p.stem in seen:
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and isinstance(d.get("id"), str) and isinstance(d.get("first_seen"), str):
            seen.add(p.stem)
            out.append(d)
    return out


def place(segment: dict | None) -> str:
    seg = segment or {}
    return " x ".join(str(seg[d]) for d in sc.DIMS if seg.get(d)) or "everywhere"


def describe(i: dict) -> dict:
    """What one output file says, in the fields the page shows."""
    hyps = [h for h in i.get("hypotheses") or [] if isinstance(h, dict)]
    lead = hyps[0] if hyps and hyps[0].get("status") != "rejected" else None
    return {
        "id": i["id"], "status": i.get("status", "open"), "metric": i.get("metric"), "moved": sc._moved(i),
        "place": place(i.get("segment")), "segment": i.get("segment") or {},
        "first_seen": i.get("first_seen"), "last_seen": i.get("last_seen"),
        "supported": [str(h.get("cause")) for h in hyps if h.get("status") == "supported"],
        "leading": None if lead is None else f"{lead.get('cause')} ({lead.get('status')})",
        "recurrence_of": i.get("recurrence_of"), "innocent": i.get("innocent"),
    }


# ----------------------------------------------------------------------------- checks (output files only)
def _detected(ctx) -> tuple[bool, str]:
    pf = ctx["score"]["per_fault"]
    n = sum(p["detected"] for p in pf)
    return n == len(pf) and n > 0, f"{n} of {len(pf)} fault(s) detected"


def _localized(ctx):
    pf = ctx["score"]["per_fault"]
    n = sum(bool(p.get("localized")) for p in pf)
    return n == len(pf) and n > 0, f"{n} of {len(pf)} localized to the injected place"


def _cause(ctx):
    pf = ctx["score"]["per_fault"]
    n = sum(bool(p.get("correct_cause")) for p in pf)
    return n == len(pf) and n > 0, f"{n} of {len(pf)} with the right cause (sim.score rule)"


def _detecting(ctx) -> list[dict]:
    ids = [p.get("incident") for p in ctx["score"]["per_fault"] if p["detected"]]
    return [i for i in ctx["incidents"] if i["id"] in ids]


def _no_supported_release(ctx):
    bad = [i["id"] for i in _detecting(ctx) for h in i.get("hypotheses") or []
           if h.get("status") == "supported" and h.get("kind") == "release"]
    return not bad and bool(_detecting(ctx)), ("no release is called the cause" if not bad
                                               else f"a release is supported in {', '.join(bad)}")


def _late_release_not_supported(ctx):
    """The fault's release row is three hours after its onset: it must not be the supported cause."""
    late = [e for e in ctx["events"] if e.scope.get("release") == "late"]
    texts = [f"({e.scope.get('country')}, {e.scope.get('platform')})" for e in late]
    bad = [i["id"] for i in _detecting(ctx) for h in i.get("hypotheses") or []
           if h.get("status") == "supported" and h.get("kind") == "release" and any(t in str(h.get("cause")) for t in texts)]
    return not bad and bool(_detecting(ctx)), ("the release that came after the onset is not supported" if not bad
                                               else f"the late release is supported in {', '.join(bad)}")


def _silent(ctx):
    s = ctx["score"]
    return s["innocents_silent"] == s["innocents"] and s["innocents"] > 0, \
        f"{s['innocents_silent']} of {s['innocents']} innocent event(s) silent"


def _no_false_alarm(ctx):
    fa = ctx["score"]["false_alarm_incidents"]
    return not fa, "no false alarm" if not fa else "false alarm(s): " + ", ".join(
        f"{f['id']} {f['metric']} in {place(f['segment'])}" for f in fa)


def _recurrence(ctx):
    found = sorted(_detecting(ctx), key=lambda i: i["first_seen"])
    if len(found) < 2:
        return False, "fewer than two detecting incidents"
    first, second = found[0], found[-1]
    ok = second.get("recurrence_of") == first["id"]
    return ok, (f"{second['id']} is linked to {first['id']}" if ok
                else f"{second['id']} carries recurrence_of={second.get('recurrence_of')!r}, not {first['id']}")


CHECKS = {"detected": _detected, "localized": _localized, "cause": _cause, "no_supported_release": _no_supported_release,
          "late_release_not_supported": _late_release_not_supported, "silent": _silent, "no_false_alarm": _no_false_alarm,
          "recurrence": _recurrence}


def judge(case: Case, events: list, outputs: list[dict], data_end: pd.Timestamp, days: float) -> dict:
    """Score the brain's output files against the recorded events and check the case's expectation."""
    counted = [i for i in outputs if i.get("status", "open") in COUNTED]
    records = [i for i in outputs if i.get("status", "open") not in COUNTED]
    result = sc.score(events, outputs, data_end, days)
    ctx = {"events": events, "incidents": counted, "score": result}
    checks = []
    for name in case.checks:
        ok, detail = CHECKS[name](ctx)
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
    if case.max_incidents is not None:
        ok = len(counted) <= case.max_incidents
        checks.append({"check": "incidents", "ok": ok, "detail": f"{len(counted)} incident(s), at most {case.max_incidents} expected"})
    return {
        "case": case.name, "injected": case.injected, "expectation": case.expect,
        "events": [{"kind": e.kind, **{k: v for k, v in e.scope.items()}} for e in events],
        "incidents": [describe(i) for i in counted], "records": [describe(i) for i in records],
        "score": {k: result[k] for k in ("faults", "detected", "localized_rate", "correct_cause_rate", "false_alarms",
                                         "innocents", "innocents_silent")},
        "per_fault": result["per_fault"], "checks": checks, "pass": all(c["ok"] for c in checks),
    }


# ----------------------------------------------------------------------------- the cases
def _h(t0, hours):
    return t0 + pd.Timedelta(hours=hours)


CASES = [
    Case("traffic_drop", "traffic_drop DE x Android, sessions -50 % for 6 h, every rate unchanged, no release",
         "detected in DE x Android, and the report or a hypothesis names sessions or traffic as what moved",
         lambda con, t0: inject.traffic_drop(con, "DE", "Android", t0, _h(t0, 6), severity=0.5, replay_now=False),
         ["detected", "localized", "cause", "no_false_alarm"], max_incidents=1),
    Case("small_payment_fault", "payment_failure DE x Android, severity 0.085 (failures about 6 % -> 14 %) for 8 h, "
         "with its release at the onset",
         "detected in DE x Android with the payments-sdk release supported",
         lambda con, t0: inject.payment_failure(con, "DE", "Android", t0, severity=0.085, end=_h(t0, 8), replay_now=False),
         ["detected", "localized", "cause", "no_false_alarm"], max_incidents=1),
    Case("payment_during_promo", "FR promo 08:00-20:00 (in the calendar) and payment_failure FR x iOS, severity 0.4, "
         "11:00-17:00",
         "the payment fault is detected in FR x iOS with its release supported; the promo itself raises nothing",
         lambda con, t0: (inject.promo(con, "FR", t0, _h(t0, 12), replay_now=False),
                          inject.payment_failure(con, "FR", "iOS", _h(t0, 3), severity=0.4, end=_h(t0, 9), replay_now=False)),
         ["detected", "localized", "cause", "silent", "no_false_alarm"], max_incidents=1),
    Case("mix_shift", "mix_shift: electronics gets 50 % of sessions (normally 25 %) for 8 h, every per-category rate "
         "unchanged, not in the calendar",
         "no incident: the change is innocent (an explained record is fine)",
         lambda con, t0: inject.mix_shift(con, "electronics", t0, _h(t0, 8), share=0.5, replay_now=False),
         ["silent", "no_false_alarm"], max_incidents=0),
    Case("feed_unavailable", "feed_unavailable: out-of-stock views x12 and feed clicks +25 % in channel=recommendations "
         "for 8 h, with a 'recommendations model 3.1 rollout' release at the onset",
         "detected in channel=recommendations with the recommendations release supported, one incident",
         lambda con, t0: inject.feed_unavailable(con, t0, _h(t0, 8), multiplier=12.0, replay_now=False),
         ["detected", "localized", "cause", "no_false_alarm"], max_incidents=1),
    Case("discount_push", "discount_push: discount per euro +60 % and conversion +15 % in channel=recommendations for "
         "8 h (orders up, contribution down), with a 'recommendations ranking 3.2 rollout' release at the onset",
         "detected in channel=recommendations with the recommendations release supported, one incident",
         lambda con, t0: inject.discount_push(con, t0, _h(t0, 8), lift=0.6, replay_now=False),
         ["detected", "localized", "cause", "no_false_alarm"], max_incidents=1),
    Case("payment_no_release", "payment_failure DE x Web, severity 0.4 for 6 h, with no release row at all",
         "detected in DE x Web; no release is called the cause (the report says no cause is confirmed)",
         lambda con, t0: inject.payment_failure(con, "DE", "Web", t0, severity=0.4, end=_h(t0, 6), replay_now=False,
                                                release="none"),
         ["detected", "localized", "no_supported_release", "no_false_alarm"], max_incidents=1),
    Case("payment_release_late", "payment_failure ES x Android, severity 0.4 for 6 h; its release row is written 3 h "
         "after the onset",
         "detected in ES x Android; the release that came after the onset is not called the cause",
         lambda con, t0: inject.payment_failure(con, "ES", "Android", t0, severity=0.4, end=_h(t0, 6), replay_now=False,
                                                release="late"),
         ["detected", "localized", "late_release_not_supported", "no_false_alarm"], max_incidents=1),
    Case("sibling_stockouts", "stockout_surge in home, x12, in DE and in FR only (two sibling places), 8 h",
         "both detected, each in home x its country, as at most two incidents",
         lambda con, t0: (inject.stockout_surge(con, "home", t0, _h(t0, 8), multiplier=12.0, country="DE", replay_now=False),
                          inject.stockout_surge(con, "home", t0, _h(t0, 8), multiplier=12.0, country="FR", replay_now=False)),
         ["detected", "localized", "cause", "no_false_alarm"], max_incidents=2),
    Case("recurrence", "payment_failure DE x Android, severity 0.4, 08:00-14:00 on two days in a row (18 h apart), "
         "each with its release",
         "both detected with the release supported; the second incident is linked to the first (recurrence_of)",
         lambda con, t0: (inject.payment_failure(con, "DE", "Android", t0, severity=0.4, end=_h(t0, 6), replay_now=False),
                          inject.payment_failure(con, "DE", "Android", _h(t0, 24), severity=0.4, end=_h(t0, 30),
                                                 replay_now=False)),
         ["detected", "localized", "cause", "recurrence", "no_false_alarm"], max_incidents=2),
    Case("payment_with_late_source", "payment_failure DE x Web, severity 0.4 for 6 h, while the payments source "
         "arrives 2 h late over the same hours",
         "detected in DE x Web with the release supported (later is fine); the late source raises nothing of its own",
         lambda con, t0: (inject.payment_failure(con, "DE", "Web", t0, severity=0.4, end=_h(t0, 6), replay_now=False),
                          inject.data_delay(con, "payments", t0, _h(t0, 6), delay_hours=2, replay_now=False)),
         ["detected", "localized", "cause", "silent", "no_false_alarm"], max_incidents=1),
]


# ----------------------------------------------------------------------------- building and running
def build(case: Case, path: Path, days: int = DAYS, event_day: int = EVENT_DAY) -> tuple[pd.Timestamp, pd.Timestamp]:
    """A fresh seeded database with the routine decoy releases and the case's events, generated in one pass.
    Returns (first event hour, last hour of data)."""
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    start = pd.Timestamp(DEFAULT_START)
    t0 = start + pd.Timedelta(days=event_day) + pd.Timedelta(hours=EVENT_HOUR)
    con = db.connect(path)
    try:
        db.init_db(con, case.seed, start, ("DE", "FR", "ES", "PL"))
        db.add_release_context(con, days * 24)
        case.build(con, t0)
        db.write_hours(con, 0, days * 24)
        last = db.last_hour(con)
    finally:
        con.close()
    return t0, last


def replay_window(events: list, last: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Clock values for the replay: from LEAD_HOURS before the first event to TAIL_HOURS after the last one ends."""
    first = min(e.start for e in events)
    ends = [e.end or last for e in events]
    return first - LEAD_HOURS * HOUR, min(max(ends) + TAIL_HOURS * HOUR, last + HOUR)


def run_brain(dbp: Path, folder: Path, clock_from: pd.Timestamp, clock_to: pd.Timestamp, timeout: float = 600) -> str:
    cmd = [sys.executable, "-m", "brain.run", "--replay", "--db", str(dbp), "--incidents", str(folder), "--fresh",
           "--quiet", "--from", _fmt(clock_from), "--to", _fmt(clock_to)]
    done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    if done.returncode != 0:
        raise RuntimeError(f"brain.run failed ({done.returncode}): {done.stderr[-2000:]}")
    return done.stdout.strip().splitlines()[-1] if done.stdout.strip() else ""


def run_case(case: Case, work: Path, days: int = DAYS, event_day: int = EVENT_DAY) -> dict:
    began = time.time()
    dbp, folder = work / f"{case.name}.db", work / f"{case.name}-incidents"
    _, last = build(case, dbp, days, event_day)
    events, start, data_end = sc.load_truth(dbp)
    clock_from, clock_to = replay_window(events, last)
    brain_line = run_brain(dbp, folder, clock_from, clock_to)
    outputs = read_outputs(folder)
    seen_end = min(data_end, clock_to - HOUR)                # the newest data hour the brain looked at
    row = judge(case, events, outputs, seen_end, (seen_end - start + HOUR) / pd.Timedelta(days=1))
    row.update({"replay": {"from": _fmt(clock_from), "to": _fmt(clock_to), "brain": brain_line,
                           "seconds": round(time.time() - began, 1)}, "seed": case.seed})
    (work / f"{case.name}.md").write_text(
        sc.report(sc.score(events, outputs, seen_end, (seen_end - start + HOUR) / pd.Timedelta(days=1)), dbp,
                  len(outputs), (seen_end - start + HOUR) / pd.Timedelta(days=1)), encoding="utf-8")
    return row


# ----------------------------------------------------------------------------- the page
def _cell(text: str) -> str:
    return str(text).replace("|", "/").replace("\n", " ")


def page(rows: list[dict]) -> str:
    passed = sum(r["pass"] for r in rows)
    lines = [
        "# Stress cases", "",
        f"Made by `python scripts/stress_cases.py`: {len(rows)} targeted cases, {passed} pass their stated expectation. "
        f"Each case is a seeded {DAYS}-day simulated history with routine decoy releases and one event (or the pair the "
        "case is about), replayed by `python -m brain.run --replay` over the hours around the event and judged only from "
        "the files the brain wrote, with the sim.score rules. A failed case is what the system does today. The data is "
        "simulated.", "",
        "| Case | Injected | Expected | Reported | Result |", "|---|---|---|---|---|",
    ]
    for r in rows:
        said = []
        for i in r["incidents"]:
            cause = i["supported"][0] if i["supported"] else (i["leading"] or "no cause confirmed")
            extra = f", recurrence of {i['recurrence_of']}" if i.get("recurrence_of") else ""
            said.append(f"{i['id']} {i['moved'] or i['metric']} in {i['place']} ({i['status']}{extra}): {cause}")
        for i in r["records"]:
            why = (i.get("innocent") or {}).get("reason") if isinstance(i.get("innocent"), dict) else None
            said.append(f"{i['status']} record {i['metric']} in {i['place']}" + (f": {why}" if why else ""))
        verdict = "pass" if r["pass"] else "FAIL: " + "; ".join(c["detail"] for c in r["checks"] if not c["ok"])
        lines.append(f"| {r['case']} | {_cell(r['injected'])} | {_cell(r['expectation'])} | "
                     f"{_cell('; '.join(said) or 'nothing')} | {_cell(verdict)} |")
    lines += ["", "## Checks per case", ""]
    for r in rows:
        lines.append(f"- **{r['case']}** (seed {r['seed']}, replay {r['replay']['from']} to {r['replay']['to']}): " +
                     "; ".join(f"{c['check']} {'ok' if c['ok'] else 'FAILED'} ({c['detail']})" for c in r["checks"]))
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", help="comma-separated case names (default: all): " + ", ".join(c.name for c in CASES))
    ap.add_argument("--work", default="data/stress", help="folder for the case databases and incident folders")
    ap.add_argument("--out-dir", default="docs", help="where stress.md and stress.json are written")
    a = ap.parse_args(argv)
    names = [n.strip() for n in a.cases.split(",")] if a.cases else [c.name for c in CASES]
    unknown = sorted(set(names) - {c.name for c in CASES})
    if unknown:
        ap.error(f"unknown case(s): {', '.join(unknown)}")
    work, out = (ROOT / a.work if not Path(a.work).is_absolute() else Path(a.work)), \
        (ROOT / a.out_dir if not Path(a.out_dir).is_absolute() else Path(a.out_dir))
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for case in [c for c in CASES if c.name in names]:
        try:
            row = run_case(case, work)
        except Exception as exc:          # one broken case must not cost the others: it is reported as failed
            row = {"case": case.name, "injected": case.injected, "expectation": case.expect, "events": [],
                   "incidents": [], "records": [], "score": {}, "per_fault": [], "pass": False, "seed": case.seed,
                   "checks": [{"check": "run", "ok": False, "detail": f"the case did not run: {exc}"[:500]}],
                   "replay": {"from": "", "to": "", "brain": "", "seconds": 0}}
        print(f"{case.name}: {'pass' if row['pass'] else 'FAIL'} ({row['replay']['seconds']} s) "
              + "; ".join(c["detail"] for c in row["checks"]), flush=True)
        rows.append(row)
    (out / "stress.json").write_text(json.dumps({"days": DAYS, "cases": rows}, indent=2, default=str) + "\n",
                                     encoding="utf-8", newline="\n")
    (out / "stress.md").write_text(page(rows), encoding="utf-8", newline="\n")
    print(f"{sum(r['pass'] for r in rows)} of {len(rows)} cases pass; wrote {out / 'stress.md'} and {out / 'stress.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
