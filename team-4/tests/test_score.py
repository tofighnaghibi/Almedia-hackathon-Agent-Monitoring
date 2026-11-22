"""Tests for the scoring harness, using made-up incidents against a generated truth."""
import json

import pandas as pd
import pytest

from sim import score as sc


@pytest.fixture(scope="module")
def truth(tmp_path_factory):
    path = sc.generate(tmp_path_factory.mktemp("eval") / "eval.db", faults=4, innocents=4, seed=3)
    events, start, end = sc.load_truth(path)
    return path, events, start, end, (end - start) / pd.Timedelta(days=1)


def faults_of(events):
    return [e for e in events if e.kind in sc.FAULT_KINDS]


def payment(events):
    return next(e for e in events if e.kind == "payment_failure")


CAUSES = {"search_regression": ("release search ranking v2 in {country}", "release"),
          "ad_budget_exhausted": ("pacing controller changed ad_load", "metric"),
          "stockout_surge": ("stockouts rose in the category", "metric")}


def incident(n, f, hours_after=2, segment=None, supported=True, cause=None):
    seg = {d: f.scope[d] for d in sc.DIMS if d in f.scope} if segment is None else segment
    if f.kind == "payment_failure":
        text, kind = f"release payments-sdk 4.2 for {f.scope['platform']} in {f.scope['country']}", "release"
    else:
        text, kind = CAUSES[f.kind][0].format(country=f.scope.get("country")), CAUSES[f.kind][1]
    return {"id": f"inc-{n}", "first_seen": (f.start + pd.Timedelta(hours=hours_after)).strftime("%Y-%m-%dT%H:00:00Z"),
            "segment": seg, "hypotheses": [{"cause": cause or text, "kind": kind, "status": "supported" if supported else "rejected"}]}


def test_generated_events_fit_their_slots_and_do_not_overlap(truth):
    _, events, start, end, _ = truth
    assert len(events) == 8 and len(faults_of(events)) == 4
    assert {e.kind for e in faults_of(events)} == set(sc.FAULT_KINDS)       # one of each kind
    ordered = sorted(events, key=lambda e: e.start)
    for a, b in zip(ordered, ordered[1:]):
        assert a.end <= b.start
    assert ordered[0].start >= start + pd.Timedelta(days=21)         # three clean baseline weeks first
    assert all(e.end <= end for e in events)
    from sim import db
    con = db.connect(truth[0])
    try:
        assert con.execute("SELECT COUNT(*) FROM releases").fetchone()[0] >= 49
        assert con.execute("SELECT COUNT(*) FROM releases WHERE ts < ?", ((start + pd.Timedelta(days=21)).strftime("%Y-%m-%dT%H:00:00Z"),)).fetchone()[0] == 21
    finally:
        con.close()


def test_generate_is_reproducible(tmp_path):
    a = sc.load_truth(sc.generate(tmp_path / "a.db", faults=3, innocents=2, seed=9))[0]
    b = sc.load_truth(sc.generate(tmp_path / "b.db", faults=3, innocents=2, seed=9))[0]
    assert [(e.kind, e.start, e.scope) for e in a] == [(e.kind, e.start, e.scope) for e in b]


def test_a_perfect_brain_scores_perfectly(truth):
    _, events, _, end, days = truth
    incs = [incident(n, f, hours_after=2) for n, f in enumerate(faults_of(events))]
    r = sc.score(events, incs, end, days)
    assert (r["detection_rate"], r["localized_rate"], r["correct_cause_rate"]) == (1, 1, 1)
    assert r["mean_delay_h"] == 2 and r["false_alarms"] == 0 and r["innocent_silent_rate"] == 1


def test_a_silent_brain_detects_nothing_but_raises_no_alarm(truth):
    _, events, _, end, days = truth
    r = sc.score(events, [], end, days)
    assert r["detection_rate"] == 0 and r["mean_delay_h"] is None and r["false_alarms"] == 0
    assert r["innocent_silent_rate"] == 1 and r["localized_rate"] is None


def test_country_only_incident_is_detected_but_not_localized(truth):
    _, events, _, end, days = truth
    f = payment(events)
    r = sc.score(events, [incident(1, f, segment={"country": f.scope["country"]})], end, days)
    assert r["detected"] == 1 and r["localized_rate"] == 0


def test_wrong_cause_is_not_counted_as_correct(truth):
    _, events, _, end, days = truth
    f = payment(events)
    r = sc.score(events, [incident(1, f, supported=False), ], end, days)
    assert r["detected"] == 1 and r["correct_cause_rate"] == 0
    r = sc.score(events, [incident(1, f, cause="traffic drop")], end, days)
    assert r["correct_cause_rate"] == 0


def test_an_incident_before_the_fault_does_not_count_as_detection(truth):
    _, events, _, end, days = truth
    f = payment(events)
    r = sc.score(events, [incident(1, f, hours_after=-3)], end, days)
    assert r["detected"] == 0 and r["false_alarms"] == 1


def test_false_alarms_are_named_so_a_report_can_list_them(truth):
    _, events, _, end, days = truth
    f = payment(events)
    early = incident(2, f, hours_after=-3)
    r = sc.score(events, [incident(1, f), early], end, days)
    assert r["detected"] == 1 and r["false_alarms"] == 1
    assert r["false_alarm_incidents"] == [{"id": "inc-2", "first_seen": early["first_seen"], "segment": early["segment"],
                                           "metric": None, "status": None}]
    assert sc.score(events, [incident(1, f)], end, days)["false_alarm_incidents"] == []


def test_false_alarms_that_share_an_id_keep_their_own_metric_and_status(truth):
    """Two files may carry one id; each false alarm is described from its own incident, the driver before the outcome."""
    _, events, _, end, days = truth
    f = payment(events)
    a = dict(incident(1, f, hours_after=-3), status="resolved", metric="contribution", drivers=[{"metric": "ad_ctr"}])
    b = dict(incident(1, f, hours_after=-2), status="open", metric="take_rate")
    r = sc.score(events, [a, b], end, days)
    assert [(x["id"], x["metric"], x["status"]) for x in r["false_alarm_incidents"]] == [
        ("inc-1", "ad_ctr", "resolved"), ("inc-1", "take_rate", "open")]


def test_a_detected_fault_names_its_incident_and_how_many_causes_the_report_supports(truth):
    _, events, _, end, days = truth
    f = payment(events)
    i = incident(1, f)
    i["hypotheses"] += [{"cause": "release payments-sdk: routine rollout", "kind": "release", "status": "supported"},
                        {"cause": "late or missing data", "kind": "freshness", "status": "rejected"}]
    [p] = [p for p in sc.score(events, [i], end, days)["per_fault"] if p["detected"]]
    assert p["correct_cause"] and p["incident"] == "inc-1" and p["supported_causes"] == 2


def test_the_supported_causes_are_counted_in_the_incident_that_detected_the_fault(truth):
    _, events, _, end, days = truth
    f = payment(events)
    stray = incident(1, f, hours_after=-3)       # first in the list, one supported cause, not the detection
    hit = incident(2, f)
    hit["hypotheses"] += [{"cause": "stockouts rose in the category", "kind": "metric", "status": "supported"},
                          {"cause": "a shift in the mix", "kind": "metric", "status": "plausible"},
                          {"cause": "late or missing data", "kind": "freshness", "status": "rejected"}]
    [p] = [p for p in sc.score(events, [stray, hit], end, days)["per_fault"] if p["detected"]]
    assert p["incident"] == "inc-2" and p["supported_causes"] == 2


def test_the_single_run_report_does_not_land_on_the_many_run_page_by_default(truth, tmp_path, monkeypatch):
    path, events, _, _, _ = truth
    folder = tmp_path / "inc"
    folder.mkdir()
    (folder / "inc-1.json").write_text(json.dumps(incident(1, payment(events))), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert sc.main(["score", "--db", str(path), "--incidents", str(folder)]) == 0
    assert (tmp_path / "data" / "eval-results.md").is_file() and not (tmp_path / "docs" / "results.md").exists()


def test_alarm_during_a_promo_in_its_country_breaks_silence(truth):
    _, events, _, end, days = truth
    promo = next(e for e in events if e.kind == "promo")
    noisy = {"id": "inc-x", "first_seen": (promo.start + pd.Timedelta(hours=1)).strftime("%Y-%m-%dT%H:00:00Z"),
             "segment": {"country": promo.scope["country"]}, "hypotheses": []}
    r = sc.score(events, [noisy], end, days)
    assert r["false_alarms"] == 1 and r["innocents_silent"] == r["innocents"] - 1


def test_cli_writes_the_report(truth, tmp_path):
    path, events, *_ = truth
    folder = tmp_path / "incidents"
    folder.mkdir()
    for n, f in enumerate(faults_of(events)):
        (folder / f"{n}.json").write_text(json.dumps(incident(n, f)))
    out = tmp_path / "results.md"
    assert sc.main(["score", "--db", str(path), "--incidents", str(folder), "--out", str(out)]) == 0
    text = out.read_text()
    assert "Faults detected | 4 of 4 (100%)" in text and "The data is simulated" in text


def test_each_fault_kind_needs_its_own_cause_to_count_as_right(truth):
    _, events, _, end, days = truth
    for f in faults_of(events):
        right = sc.score(events, [incident(1, f)], end, days)
        assert right["correct_cause_rate"] == 1, f.kind
        wrong = sc.score(events, [incident(1, f, cause="traffic drop")], end, days)
        assert wrong["detected"] == 1 and wrong["correct_cause_rate"] == 0, f.kind


def test_a_wider_incident_is_not_contradicted_but_a_different_country_is(truth):
    _, events, _, end, days = truth
    f = next(e for e in events if e.kind == "search_regression")                   # scope: country only
    ok = sc.score(events, [incident(1, f, segment={"country": f.scope["country"], "platform": "iOS"})], end, days)
    assert ok["detected"] == 1 and ok["localized_rate"] == 1
    other = "FR" if f.scope["country"] != "FR" else "DE"
    off = sc.score(events, [incident(1, f, segment={"country": other})], end, days)
    assert off["detected"] == 0 and off["false_alarms"] == 1


def test_an_incident_long_after_a_fault_ended_does_not_detect_it(truth):
    _, events, _, end, days = truth
    f = payment(events)
    late = (f.end - f.start) / pd.Timedelta(hours=1) + 7                    # 7 hours after the fault ended
    r = sc.score(events, [incident(1, f, hours_after=late)], end, days)
    assert r["detected"] == 0 and r["false_alarms"] == 1
    soon = sc.score(events, [incident(1, f, hours_after=(f.end - f.start) / pd.Timedelta(hours=1) + 5)], end, days)
    assert soon["detected"] == 1


def test_one_incident_cannot_detect_two_faults(truth):
    _, events, _, end, days = truth
    first = payment(events)
    twin = sc.Event("payment_failure", first.start + pd.Timedelta(hours=1), first.end, dict(first.scope))
    r = sc.score([first, twin], [incident(1, first, hours_after=2)], end, days)
    assert r["faults"] == 2 and r["detected"] == 1


# ------------------------------------------------------- statuses explained and pending
def test_explained_and_pending_incidents_are_skipped_by_the_scorer(truth):
    _, events, _, end, days = truth
    promo = next(e for e in events if e.kind == "promo")
    delay = next(e for e in events if e.kind == "data_delay")
    start = lambda e, h: (e.start + pd.Timedelta(hours=h)).strftime("%Y-%m-%dT%H:00:00Z")
    explained = {"id": "inc-e", "status": "explained", "first_seen": start(promo, 1), "segment": {"country": promo.scope["country"]}, "hypotheses": []}
    pending = {"id": "inc-p", "status": "pending", "first_seen": start(delay, 1), "segment": {}, "hypotheses": []}
    r = sc.score(events, [explained, pending], end, days)
    assert r["false_alarms"] == 0 and r["detected"] == 0
    assert r["innocents_silent"] == r["innocents"]
    f = payment(events)
    counted = dict(explained, status="open")                                # the same file as an alarm in the promo window
    assert sc.score(events, [counted], end, days)["false_alarms"] == 1
    assert sc.score(events, [incident(1, f)], end, days)["detected"] == 1  # no status at all still counts


def test_a_hypothesis_without_a_kind_does_not_count_for_a_release_cause(truth):
    _, events, _, end, days = truth
    f = payment(events)
    inc = incident(1, f)
    for h in inc["hypotheses"]:
        h.pop("kind")
    r = sc.score(events, [inc], end, days)
    assert r["detected"] == 1 and r["correct_cause_rate"] == 0


def test_second_fault_is_not_consumed_by_previous_fault():
    s = pd.Timestamp('2026-11-20T08:00:00Z')
    first = sc.Event('stockout_surge', s, s + pd.Timedelta(hours=4), {'category': 'home'})
    second = sc.Event('payment_failure', s + pd.Timedelta(hours=6), s + pd.Timedelta(hours=10),
                      {'country': 'DE', 'platform': 'Android'})
    r = sc.score([first, second], [incident(1, first, 1), incident(2, second, 1)], s + pd.Timedelta(days=1), 1)
    assert r['detected'] == 2 and r['false_alarms'] == 0


def test_duplicate_ids_in_different_files_do_not_hide_false_alarms(tmp_path):
    for n in range(2):
        (tmp_path / f'{n}.json').write_text(json.dumps({'id': 'same', 'first_seen': '2026-11-20T08:00:00Z'}))
    r = sc.score([], sc.load_incidents(tmp_path), pd.Timestamp('2026-11-21T00:00:00Z'), 1)
    assert r['false_alarms'] == 2


@pytest.mark.parametrize('value', [[], {}, {'id': 'x'}, {'id': 'x', 'first_seen': '2026-11-20T08:00:00'},
    {'id': 'x', 'first_seen': '2026-11-20T08:00:00Z', 'hypotheses': None},
    {'id': 'x', 'first_seen': '2026-11-20T08:00:00Z', 'segment': 'DE'}])
def test_bad_file_names_its_path(tmp_path, value):
    p = tmp_path / 'broken.json'
    p.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='broken.json'):
        sc.load_incidents(tmp_path)


def test_missing_or_empty_incidents_never_publish_results(tmp_path):
    for folder in (tmp_path / 'missing', tmp_path):
        out = tmp_path / 'results.md'
        with pytest.raises(SystemExit) as exc:
            sc.main(['score', '--incidents', str(folder), '--out', str(out)])
        assert exc.value.code == 2 and not out.exists()


def test_routine_releases_have_no_effect_on_facts(tmp_path):
    from sim.run import backfill
    from sim import db
    a = backfill(tmp_path / 'plain.db', 48, countries=('DE',), add_context=False)
    b = backfill(tmp_path / 'context.db', 48, countries=('DE',), add_context=True)
    ca, cb = db.connect(a), db.connect(b)
    try:
        assert ca.execute('SELECT * FROM facts_hourly ORDER BY ts, country, platform, category, channel').fetchall() == cb.execute('SELECT * FROM facts_hourly ORDER BY ts, country, platform, category, channel').fetchall()
        assert cb.execute('SELECT COUNT(*) FROM releases').fetchone()[0] == 2
        assert cb.execute('SELECT COUNT(*) FROM ground_truth').fetchone()[0] == 0
        assert cb.execute('SELECT COUNT(*) FROM calendar WHERE end_ts < ?', ('2026-10-25T00:00:00Z',)).fetchone()[0] == 2
    finally:
        ca.close(); cb.close()


def test_invalid_generation_keeps_existing_database(tmp_path):
    p = tmp_path / 'keep.db'
    p.write_bytes(b'keep me')
    with pytest.raises(ValueError):
        sc.generate(p, faults=0, innocents=0)
    assert p.read_bytes() == b'keep me'


def test_contract_example_localizes_and_names_the_cause():
    from pathlib import Path
    i = json.loads(Path('docs/contracts/incident_example.json').read_text())
    start = pd.Timestamp(i['first_seen'])
    f = sc.Event('payment_failure', start, start + pd.Timedelta(hours=4), dict(i['segment']))
    r = sc.score([f], [i], start + pd.Timedelta(days=1), 1)
    assert r['localized_rate'] == r['correct_cause_rate'] == 1
