"""The stress-case harness (scripts/stress_cases.py): it judges from output files only, writes one row per case, and runs
end to end on one tiny case. The full run (eleven 30-day cases) is a script, not a test."""
import json

import pandas as pd
import pytest

from scripts import stress_cases as st
from sim import inject
from sim import score as sc

T0 = pd.Timestamp("2026-11-21T08:00:00Z")
END = pd.Timestamp("2026-11-23T23:00:00Z")


def event(kind, hours=6, **scope):
    return sc.Event(kind, T0, T0 + pd.Timedelta(hours=hours),
                    {"start": T0.strftime("%Y-%m-%dT%H:00:00Z"), "end": (T0 + pd.Timedelta(hours=hours)).strftime("%Y-%m-%dT%H:00:00Z"), **scope})


def incident(n, segment, hours=1, status="ongoing", hypotheses=(), report="", **extra):
    return {"id": f"inc-{n:04d}", "status": status, "first_seen": (T0 + pd.Timedelta(hours=hours)).strftime("%Y-%m-%dT%H:00:00Z"),
            "last_seen": (T0 + pd.Timedelta(hours=6)).strftime("%Y-%m-%dT%H:00:00Z"), "metric": "contribution",
            "segment": segment, "hypotheses": list(hypotheses), "report": report, **extra}


def case(name):
    return next(c for c in st.CASES if c.name == name)


def test_every_case_the_brief_asks_for_is_there_with_an_expectation():
    names = [c.name for c in st.CASES]
    assert names == ["traffic_drop", "small_payment_fault", "payment_during_promo", "mix_shift", "feed_unavailable",
                     "discount_push", "payment_no_release", "payment_release_late", "sibling_stockouts", "recurrence",
                     "payment_with_late_source"]
    assert all(c.expect and c.injected and (c.checks or c.max_incidents is not None) for c in st.CASES)
    assert all(set(c.checks) <= set(st.CHECKS) for c in st.CASES)


def test_a_traffic_drop_passes_only_when_the_output_names_traffic_in_the_place():
    f = [event("traffic_drop", country="DE", platform="Android", severity=0.5)]
    good = incident(1, {"country": "DE", "platform": "Android"},
                    report="sessions fell to 900 from an expected 1800 in DE x Android. No cause is confirmed yet.")
    row = st.judge(case("traffic_drop"), f, [good], END, 30.0)
    assert row["pass"] and row["incidents"][0]["place"] == "DE x Android"
    given = dict(good, report="orders fell in DE x Android. Traffic is taken as given: only rates are judged.")
    row = st.judge(case("traffic_drop"), f, [given], END, 30.0)
    assert not row["pass"] and [c["check"] for c in row["checks"] if not c["ok"]] == ["cause"]
    silent = st.judge(case("traffic_drop"), f, [], END, 30.0)
    assert not silent["pass"] and silent["checks"][0]["detail"] == "0 of 1 fault(s) detected"


def test_an_explained_record_keeps_a_mix_shift_silent_but_an_incident_does_not():
    f = [event("mix_shift", hours=8, category="electronics", share=0.5)]
    record = incident(1, {"country": "DE"}, status="explained", innocent={"kind": "mix", "reason": "mix explains 95 %"})
    row = st.judge(case("mix_shift"), f, [record], END, 30.0)
    assert row["pass"] and row["records"][0]["innocent"]["kind"] == "mix" and row["incidents"] == []
    row = st.judge(case("mix_shift"), f, [incident(2, {"country": "DE", "category": "electronics"})], END, 30.0)
    assert not row["pass"]
    assert {c["check"] for c in row["checks"] if not c["ok"]} == {"silent", "no_false_alarm", "incidents"}


def test_release_cases_check_what_is_supported():
    none = [event("payment_failure", country="DE", platform="Web", severity=0.4, release="none")]
    unsure = incident(1, {"country": "DE", "platform": "Web"}, hypotheses=[{"cause": "x", "kind": "metric", "status": "plausible"}])
    assert st.judge(case("payment_no_release"), none, [unsure], END, 30.0)["pass"]
    invented = incident(1, {"country": "DE", "platform": "Web"},
                        hypotheses=[{"cause": "release payments-sdk: payments-sdk rollout (FR, iOS)", "kind": "release", "status": "supported"}])
    assert not st.judge(case("payment_no_release"), none, [invented], END, 30.0)["pass"]
    late = [event("payment_failure", country="ES", platform="Android", severity=0.4, release="late")]
    blamed = incident(1, {"country": "ES", "platform": "Android"},
                      hypotheses=[{"cause": "release payments-sdk: payments-sdk 4.2 rollout (ES, Android)", "kind": "release", "status": "supported"}])
    assert not st.judge(case("payment_release_late"), late, [blamed], END, 30.0)["pass"]
    assert st.judge(case("payment_release_late"), late, [unsure | {"segment": {"country": "ES", "platform": "Android"}}], END, 30.0)["pass"]


def test_recurrence_needs_the_link_from_the_second_incident_to_the_first():
    first, second = event("payment_failure", country="DE", platform="Android", severity=0.4), \
        sc.Event("payment_failure", T0 + pd.Timedelta(hours=24), T0 + pd.Timedelta(hours=30),
                 {"country": "DE", "platform": "Android", "severity": 0.4,
                  "start": (T0 + pd.Timedelta(hours=24)).strftime("%Y-%m-%dT%H:00:00Z")})
    rel = [{"cause": "release payments-sdk: payments-sdk 4.2 rollout (DE, Android)", "kind": "release", "status": "supported"}]
    a = incident(1, {"country": "DE", "platform": "Android"}, hypotheses=rel, status="resolved")
    b = incident(2, {"country": "DE", "platform": "Android"}, hours=25, hypotheses=rel)
    assert not st.judge(case("recurrence"), [first, second], [a, b], END, 30.0)["pass"]
    assert st.judge(case("recurrence"), [first, second], [a, dict(b, recurrence_of="inc-0001")], END, 30.0)["pass"]


def test_outputs_are_read_leniently_and_the_page_has_one_row_per_case(tmp_path):
    assert st.read_outputs(tmp_path / "missing") == []
    folder = tmp_path / "inc"
    (folder / "notes").mkdir(parents=True)
    (folder / "inc-0001.json").write_text(json.dumps(incident(1, {"country": "DE"})), encoding="utf-8")
    (folder / "half.json").write_text('{"id": "inc-0002", "first', encoding="utf-8")
    (folder / "notes" / "explained-x.json").write_text(json.dumps(incident(3, {}, status="explained")), encoding="utf-8")
    assert [i["id"] for i in st.read_outputs(folder)] == ["inc-0001", "inc-0003"]
    rows = [st.judge(case("mix_shift"), [event("mix_shift", category="home", share=0.5)], [], END, 30.0),
            st.judge(case("traffic_drop"), [event("traffic_drop", country="DE", platform="Android")], [], END, 30.0)]
    for r in rows:
        r.update(replay={"from": "a", "to": "b", "brain": "", "seconds": 0}, seed=1)
    text = st.page(rows)
    assert "1 pass their stated expectation" in text
    assert "| mix_shift |" in text and "| traffic_drop |" in text and "FAIL: 0 of 1 fault(s) detected" in text
    json.dumps(rows, default=str)


def test_unknown_case_names_are_a_usage_error(tmp_path):
    with pytest.raises(SystemExit) as exc:
        st.main(["--cases", "nope", "--work", str(tmp_path / "w"), "--out-dir", str(tmp_path / "o")])
    assert exc.value.code == 2


def test_a_case_that_cannot_run_is_reported_as_failed_and_the_others_still_run(tmp_path, monkeypatch):
    def fake(case, work, *a, **k):
        if case.name == "traffic_drop":
            raise RuntimeError("brain.run failed (1): boom")
        row = st.judge(case, [event("mix_shift", category="home", share=0.5)], [], END, 30.0)
        row.update(replay={"from": "a", "to": "b", "brain": "", "seconds": 0}, seed=case.seed)
        return row
    monkeypatch.setattr(st, "run_case", fake)
    assert st.main(["--cases", "traffic_drop,mix_shift", "--work", str(tmp_path / "w"), "--out-dir", str(tmp_path / "o")]) == 0
    saved = json.loads((tmp_path / "o" / "stress.json").read_text(encoding="utf-8"))
    assert [(r["case"], r["pass"]) for r in saved["cases"]] == [("traffic_drop", False), ("mix_shift", True)]
    assert "the case did not run: brain.run failed (1): boom" in (tmp_path / "o" / "stress.md").read_text(encoding="utf-8")


def test_one_tiny_case_runs_end_to_end_with_the_real_brain(tmp_path):
    tiny = st.Case("tiny_payment", "payment_failure DE x Android, severity 0.5 for 6 h", "detected in DE x Android",
                   lambda con, t0: inject.payment_failure(con, "DE", "Android", t0, severity=0.5,
                                                          end=t0 + pd.Timedelta(hours=6), replay_now=False),
                   ["detected", "localized"], seed=3)
    row = st.run_case(tiny, tmp_path, days=6, event_day=5)
    assert row["events"][0]["kind"] == "payment_failure"
    assert row["score"]["detected"] == 1 and row["pass"], row["checks"]
    assert any(i["place"] == "DE x Android" for i in row["incidents"])
    assert (tmp_path / "tiny_payment.md").is_file() and (tmp_path / "tiny_payment-incidents").is_dir()
    assert row["replay"]["from"] == "2026-10-30T05:00:00Z"
    json.dumps(row, default=str)
