"""docs/results.md is built from scored runs. Seeds the brain never saw are kept apart from the seeds it was
built on, and the page says which is which. Every failure is listed: a miss, a wrong place, a cause that was
not confirmed, a second cause left standing beside the right one, a false alarm."""
import json
import re
import shutil
from pathlib import Path

import pandas as pd
import pytest

from scripts import summarize_evaluation as se
from sim import score as sc

ROOT = Path(__file__).resolve().parents[1]
AT = "2026-11-20T10:00:00Z"


def fault(kind, detected=True, delay=0.0, localized=True, cause=True, supported=1, incident="inc-1", **scope):
    p = {"kind": kind, "fault": {**scope, "start": AT}, "detected": detected}
    if detected:
        p.update(delay_h=delay, localized=localized, correct_cause=cause, incident=incident, supported_causes=supported)
    return p


def run(seed, per_fault, false_alarms=0, innocents=8, silent=8, days=49.0, alarms=None):
    r = dict(seed=seed, days=days, faults=len(per_fault), detected=sum(p["detected"] for p in per_fault),
             false_alarms=false_alarms, innocents=innocents, innocents_silent=silent, per_fault=per_fault)
    if alarms is not None:
        r["false_alarm_incidents"] = alarms
    return r


@pytest.fixture
def reports():
    """Seven faults in three runs: one missed, one in the wrong place, one without its cause, one tie."""
    return [
        run(4, [fault("payment_failure", country="DE", platform="Android"),
                fault("stockout_surge", cause=False, supported=0, category="sports"),
                fault("search_regression", delay=2.0, localized=False, country="FR")]),
        run(101, [fault("payment_failure", country="FR", platform="iOS"),
                  fault("search_regression", delay=1.0, country="ES")]),
        run(102, [fault("ad_budget_exhausted", detected=False, country="DE"),
                  fault("payment_failure", supported=2, incident="inc-7", country="PL", platform="Web")],
            false_alarms=1, silent=7),
    ]


def page(reports, **kw):
    return "\n".join(se.render(reports, **kw))


def section(text, title):
    return text.split(f"## {title}")[1].split("\n## ")[0]


# ------------------------------------------------------------------------------------ the summary table
def test_holdout_seeds_get_their_own_line_and_the_totals_add_up(reports):
    text = page(reports, holdout={101, 102}, commit="abc1234")
    assert "| Holdout (run once) | 2 | 3/4 | 3/3 | 3/3 | 1 | 15/16 | 0.33 |" in text
    assert "| Used while the brain was built | 1 | 3/3 | 2/3 | 2/3 | 0 | 8/8 | 0.67 |" in text
    assert "| All | 3 | 6/7 | 5/6 | 5/6 | 1 | 23/24 | 0.50 |" in text
    assert "brain at commit `abc1234`" in text
    assert "Holdout seeds (101, 102)" in text and "The other seeds (4)" in text


def test_without_holdout_seeds_the_page_claims_no_holdout(reports):
    text = page(reports)
    assert "| Holdout" not in text and "| Used while" not in text
    assert "None of these seeds is a holdout" in text
    assert "| All | 3 | 6/7 | 5/6 | 5/6 | 1 | 23/24 | 0.50 |" in text


def test_the_false_alarm_rate_is_per_day_of_data_unless_the_warm_up_is_given(reports):
    plain = page(reports, holdout={101, 102})
    assert "False alarms. holdout: 1 over 98 simulated days (0.010/day); all runs: 1 over 147 simulated days (0.007/day)." in plain
    watched = page(reports, holdout={101, 102}, warmup_hours=80)
    # 49 days less 80 hours is 45.67 watched days per run
    assert ("False alarms. holdout: 1 in 91 watched days (0.011/day); all runs: 1 in 137 watched days (0.007/day). "
            "Watched days leave out the first 80 hours of each run, before the replay's first judgement "
            "(98 and 147 days of data).") in watched


# ------------------------------------------------------------------------------------ the other tables
def test_fault_kinds_are_tallied_over_all_runs(reports):
    text = section(page(reports, holdout={101, 102}), "By fault kind (all runs)")
    assert "| payment_failure | 3/3 | 3/3 | 3/3 | 0.00 |" in text
    assert "| stockout_surge | 1/1 | 1/1 | 0/1 | 0.00 |" in text
    assert "| search_regression | 2/2 | 1/2 | 2/2 | 1.50 |" in text     # found twice, once in the wrong place
    assert "| ad_budget_exhausted | 0/1 | 0/0 | 0/0 | n/a |" in text


def test_every_run_has_its_row_and_holdout_runs_are_marked(reports):
    text = section(page(reports, holdout={101, 102}), "Per run")
    assert "| Seed 4, 49 days | 3/3 | 2 of 3 | 0 | 8/8 |" in text
    assert "| Seed 102, 49 days, holdout | 1/2 | 1 of 1 | 1 | 7/8 |" in text


# ------------------------------------------------------------------------------------ the lists of failures
def test_misses_wrong_places_and_unsupported_causes_are_listed_with_their_seed(reports):
    text = section(page(reports, holdout={101, 102}), "Misses and unsupported causes")
    assert f"- Seed 102 (holdout): ad_budget_exhausted at {AT}, country=DE: not detected (no matching monitor incident)." in text
    assert f"- Seed 4: stockout_surge at {AT}, category=sports: detected and localized; cause not confirmed." in text
    assert f"- Seed 4: search_regression at {AT}, country=FR: detected, not localized to the injected scope." in text
    assert "Seed 101" not in text and text.count("- Seed") == 3


def test_a_fault_with_nothing_right_but_the_detection_says_both(reports):
    text = page([run(7, [fault("payment_failure", localized=False, cause=False, supported=0, country="DE", platform="iOS")])])
    assert (f"- Seed 7: payment_failure at {AT}, country=DE, platform=iOS: detected, not localized to the injected scope; "
            "cause not confirmed.") in text


def test_a_report_that_supports_a_second_cause_is_counted_and_listed(reports):
    text = page(reports, holdout={101, 102})
    assert "In 1 of the 5 reports with the correct cause a second cause is supported beside it (listed below)." in text
    ties = section(text, "Reports that support more than one cause")
    assert (f"- Seed 102 (holdout): payment_failure at {AT}, country=PL, platform=Web, incident inc-7: the right cause is "
            "supported, and 1 other beside it.") in ties
    assert ties.count("- Seed") == 1
    clean = page(reports[:2])
    assert "None." in section(clean, "Reports that support more than one cause") and "a second cause is supported" not in clean


def test_supported_causes_that_are_all_wrong_are_said_to_be_wrong_and_are_not_a_tie():
    alarms = [{"id": "inc-8", "first_seen": AT, "metric": "ad_ctr", "segment": {}, "status": "resolved"},
              {"id": "inc-9", "first_seen": AT, "metric": "take_rate", "segment": {}, "status": "open"}]
    reports = [run(1, [fault("payment_failure", cause=False, supported=2, incident="inc-1", country="DE", platform="iOS"),
                       fault("payment_failure", supported=3, incident="inc-2", country="PL", platform="Web")],
                   false_alarms=2, alarms=alarms)]
    text = page(reports)
    assert "In 1 of the 1 reports with the correct cause" in text
    ties = section(text, "Reports that support more than one cause")
    assert ties.count("- Seed") == 1 and "incident inc-2: the right cause is supported, and 2 other beside it." in ties
    misses = section(text, "Misses and unsupported causes")
    assert (f"- Seed 1: payment_failure at {AT}, country=DE, platform=iOS: detected and localized; "
            "2 cause(s) supported, none of them the injected one.") in misses
    assert section(text, "False alarms").count("- Seed 1") == 2


def test_false_alarms_are_listed_with_what_the_incident_said(reports):
    reports[2]["false_alarm_incidents"] = [{"id": "inc-0001", "first_seen": "2026-11-05T20:00:00Z", "metric": "ad_ctr",
                                            "segment": {"country": "PL", "platform": "Web"}, "status": "resolved"}]
    text = section(page(reports, holdout={101, 102}), "False alarms")
    assert "- Seed 102 (holdout): inc-0001, ad_ctr in country=PL, platform=Web, first seen 2026-11-05T20:00:00Z (resolved)." in text


def test_a_false_alarm_the_scorer_did_not_itemise_is_still_counted_on_the_page(reports):
    text = section(page(reports), "False alarms")            # seed 102 has one, without details
    assert "- Seed 102: 1 false alarm(s), not itemised by the scorer." in text
    quiet = section(page(reports[:2]), "False alarms")
    assert "None." in quiet and "Seed" not in quiet


def test_the_limits_say_what_a_holdout_of_the_same_simulator_does_not_test(reports):
    limits = section(page(reports, holdout={101, 102}), "Limits")
    assert "new random seeds of the same simulator" in limits and "does not test other kinds of fault" in limits
    assert "for every fault" not in limits                 # routine deployments are not near every fault
    assert "only move the arrival time of a source" in limits and "Data that never arrives is not tested" in limits
    assert "does not check a payment release" in limits and "any system for payment" in limits   # the scorer's leniency, named


# ------------------------------------------------------------------------------------ files and arguments
def test_the_page_is_written_as_utf8_with_lf_line_ends(reports, tmp_path):
    out = tmp_path / "docs" / "results.md"
    meta = dict(holdout=[101], commit="abc1234", warmup_hours=80)
    se.write(out, se.render(reports, **meta), reports, meta)
    raw = out.read_bytes()
    assert b"\r\n" not in raw and "Results — simulated marketplace" in raw.decode("utf-8")
    assert b"\r\n" not in out.with_suffix(".json").read_bytes()
    saved = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert [r["seed"] for r in saved["runs"]] == [4, 101, 102]
    assert (saved["holdout"], saved["commit"], saved["warmup_hours"]) == ([101], "abc1234", 80)


def test_the_page_can_be_rebuilt_from_its_json(reports, tmp_path):
    out = tmp_path / "results.md"
    meta = dict(holdout=[101, 102], commit="abc1234", warmup_hours=80)
    se.write(out, se.render(reports, **meta), reports, meta)
    assert se.from_json(out.with_suffix(".json")) == out.read_text(encoding="utf-8").splitlines()


def test_the_tracked_page_is_what_the_script_makes_of_the_tracked_numbers():
    """docs/results.md is never edited by hand: it is the script's rendering of docs/results.json."""
    assert se.from_json(ROOT / "docs" / "results.json") == (ROOT / "docs" / "results.md").read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize("args, message", [
    (["--seeds", "4", "--holdout", "5"], "holdout seed 5 is not among the runs"),
    (["--seeds", "4,4"], "seeds must be distinct"),
    (["--seeds", ""], "no seeds given"),
    (["--seeds", "4,x"], "seeds must be whole numbers"),
    (["--seeds", "4"], "missing database"),
    (["--seeds", "4", "--warmup-hours=-1"], "warm-up must be a number of hours from 0"),
    (["--seeds", "4", "--warmup-hours", "nan"], "warm-up must be a number of hours from 0"),
    (["--seeds", "4", "--out", "page.json"], "the page cannot be a .json file"),
])
def test_arguments_that_cannot_give_a_page_are_refused_and_nothing_is_written(tmp_path, capsys, args, message):
    out = tmp_path / "r.md"
    args = [str(tmp_path / a) if a == "page.json" else a for a in args]
    with pytest.raises(SystemExit) as stop:
        se.main(["--runs", str(tmp_path), "--out", str(out)] + args)       # a later --out in args wins
    assert stop.value.code == 2 and message in capsys.readouterr().err
    assert not out.exists() and not list(tmp_path.glob("*.json"))


def test_a_warm_up_as_long_as_a_run_is_refused(tmp_path, capsys):
    runs, out = tmp_path / "runs", tmp_path / "r.md"
    sc.generate(runs / "seed3.db", faults=4, innocents=4, seed=3)
    (runs / "seed3-incidents").mkdir()
    (runs / "seed3-incidents" / "inc-1.json").write_text(json.dumps(
        {"id": "inc-1", "first_seen": "2026-11-01T00:00:00Z", "segment": {}, "hypotheses": []}), encoding="utf-8")
    with pytest.raises(SystemExit) as stop:
        se.main(["--runs", str(runs), "--seeds", "3", "--warmup-hours", "2000", "--out", str(out)])
    assert stop.value.code == 2 and "warm-up is not shorter than run 3" in capsys.readouterr().err and not out.exists()


def test_a_page_that_cannot_be_completed_leaves_the_old_page_and_its_json_alone(reports, tmp_path, monkeypatch):
    out = tmp_path / "results.md"
    se.write(out, se.render(reports), reports)
    before = out.read_bytes(), out.with_suffix(".json").read_bytes()

    def locked(*a):
        raise PermissionError("locked")
    monkeypatch.setattr(se.os, "replace", locked)
    with pytest.raises(PermissionError):
        se.write(out, se.render(reports[:1]), reports[:1])
    assert (out.read_bytes(), out.with_suffix(".json").read_bytes()) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["results.json", "results.md"]     # no temporary file is left


def test_two_runs_are_scored_from_their_own_files_and_the_json_rebuilds_the_page(tmp_path):
    runs = tmp_path / "runs"
    for seed, hits in ((3, 4), (5, 2)):
        path = sc.generate(runs / f"seed{seed}.db", faults=4, innocents=4, seed=seed)
        events, _, _ = sc.load_truth(path)
        folder = runs / f"seed{seed}-incidents"
        folder.mkdir()
        faults = [e for e in events if e.kind in sc.FAULT_KINDS]
        for n, f in enumerate(faults[:hits]):
            body = {"id": f"inc-{n}", "first_seen": (f.start + pd.Timedelta(hours=1)).strftime("%Y-%m-%dT%H:00:00Z"),
                    "segment": {d: f.scope[d] for d in sc.DIMS if d in f.scope}, "hypotheses": []}
            (folder / f"inc-{n}.json").write_text(json.dumps(body), encoding="utf-8")
    out = tmp_path / "results.md"
    assert se.main(["--runs", str(runs), "--seeds", "3,5", "--holdout", "5", "--commit", "abc1234", "--warmup-hours", "80",
                    "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "| Seed 3, 49 days | 4/4 | 0 of 4 | 0 |" in text
    assert "| Seed 5, 49 days, holdout | 2/4 | 0 of 2 | 0 |" in text
    saved = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert (saved["holdout"], saved["commit"], saved["warmup_hours"]) == ([5], "abc1234", 80)
    assert se.from_json(out.with_suffix(".json")) == text.splitlines()


def test_a_run_without_incident_files_is_an_error_not_a_measured_zero(tmp_path, capsys):
    sc.generate(tmp_path / "seed3.db", faults=4, innocents=4, seed=3)
    out = tmp_path / "r.md"
    with pytest.raises(SystemExit) as stop:
        se.main(["--runs", str(tmp_path), "--seeds", "3", "--out", str(out)])
    assert stop.value.code == 2 and "incidents folder does not exist" in capsys.readouterr().err and not out.exists()


def test_real_files_are_scored_and_summarized(tmp_path):
    """One generated truth and made-up incidents for its faults, through the command line."""
    runs = tmp_path / "runs"
    path = sc.generate(runs / "seed3.db", faults=4, innocents=4, seed=3)
    events, _, _ = sc.load_truth(path)
    folder = runs / "seed3-incidents"
    folder.mkdir()
    faults = [e for e in events if e.kind in sc.FAULT_KINDS]
    for n, f in enumerate(faults):
        body = {"id": f"inc-{n}", "first_seen": (f.start + pd.Timedelta(hours=1)).strftime("%Y-%m-%dT%H:00:00Z"),
                "segment": {d: f.scope[d] for d in sc.DIMS if d in f.scope},
                "hypotheses": [{"cause": "nothing that names a cause", "kind": "metric", "status": "plausible"}]}
        (folder / f"inc-{n}.json").write_text(json.dumps(body), encoding="utf-8")
    # a stray incident on a clean day; its own top-level metric is the outcome, its driver is what moved
    stray = {"id": "inc-stray", "first_seen": (faults[0].start - pd.Timedelta(days=5)).strftime("%Y-%m-%dT%H:00:00Z"),
             "segment": {"country": "PL"}, "status": "resolved", "metric": "contribution",
             "drivers": [{"metric": "ad_ctr"}], "hypotheses": []}
    (folder / "inc-stray.json").write_text(json.dumps(stray), encoding="utf-8")
    out = tmp_path / "results.md"
    assert se.main(["--runs", str(runs), "--seeds", "3", "--holdout", "3", "--commit", "abc1234", "--warmup-hours", "80",
                    "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "| Holdout (run once) | 1 | 4/4 | 4/4 | 0/4 | 1 | 4/4 | 1.00 |" in text
    assert "| Used while" not in text and "The other seeds" not in text       # every seed here is a holdout
    assert text.count("cause not confirmed") == 4
    assert f"- Seed 3 (holdout): inc-stray, ad_ctr in country=PL, first seen {stray['first_seen']} (resolved)." in text
    assert "False alarms. all runs: 1 in 46 watched days (0.022/day)." in text


# ------------------------------------------------------------------------------------ against a plain ranking
SECOND = "finds more, alarms more"
OLD_WORDING = "as few false alarms as the brain"


def picked(there, everywhere):
    """The picked threshold's label: the rule's bound and the brain's false alarms, both counted on the page's runs."""
    return (f"threshold picked for at most 1 false alarm on the built-on seeds; the brain: {there} there, "
            f"{everywhere} in all runs")


def sweep_row(z, false_alarms, detected=2):
    return dict(z=z, runs=1, faults=3, detected=detected, localized=detected, correct=1, ties=0, false_alarms=false_alarms,
                days=49.0, watched=45.67, innocents=8, silent=7, mean_delay="0.50")


@pytest.fixture
def baseline():
    """The plain ranking on the same three runs at two thresholds. At the picked one it misses two payment faults,
    places one wrongly and raises three alarms; at 12 it misses one fault and raises seven."""
    alarm = lambda n, metric: {"id": f"base-000{n}", "first_seen": AT, "metric": metric, "segment": {}, "status": "resolved"}  # noqa: E731
    picked = [run(4, [fault("payment_failure", detected=False, country="DE", platform="Android"),
                      fault("stockout_surge", cause=False, supported=1, category="sports"),
                      fault("search_regression", delay=3.0, country="FR")],
                  false_alarms=2, silent=6, alarms=[alarm(1, "order_rate"), alarm(2, "discount_rate")]),
              run(101, [fault("payment_failure", localized=False, country="FR", platform="iOS"),
                        fault("search_regression", delay=1.0, country="ES")],
                  false_alarms=1, alarms=[alarm(3, "order_rate")]),
              run(102, [fault("ad_budget_exhausted", country="DE"),
                        fault("payment_failure", detected=False, country="PL", platform="Web")], alarms=[])]
    second = [run(4, [fault("payment_failure", country="DE", platform="Android"),
                      fault("stockout_surge", cause=False, supported=1, category="sports"),
                      fault("search_regression", delay=1.0, country="FR")], false_alarms=3, silent=5),
              run(101, [fault("payment_failure", country="FR", platform="iOS"),
                        fault("search_regression", country="ES")], false_alarms=2, silent=7),
              run(102, [fault("ad_budget_exhausted", country="DE"),
                        fault("payment_failure", detected=False, country="PL", platform="Web")], false_alarms=2)]
    return dict(z=50.0, runs=picked, second=dict(z=12.0, runs=second),
                sweep=[sweep_row(8, 5), sweep_row(50, 1), sweep_row(12, 3)], first_version=dict(se.FIRST_VERSION))


def blocks(text):
    """The section's blocks: the comparison table, what it finds by fault kind, the rule, the sweep table, the
    paragraph."""
    return section(text, "Against a plain ranking").strip("\n").split("\n\n")


def test_the_plain_ranking_comes_after_the_summary_and_leaves_every_other_line_alone(reports, baseline):
    meta = dict(holdout={101, 102}, commit="abc1234", warmup_hours=80)
    plain, both = page(reports, **meta), page(reports, baseline=baseline, **meta)
    start, end = both.index("\n\n## Against a plain ranking\n"), both.index("\n\n## By fault kind (all runs)")
    assert both[:start] + both[end:] == plain
    assert both.index("False alarms. holdout:") < start                     # the summary and its notes come first


def test_the_section_is_the_comparison_by_kind_then_the_built_on_sweep_then_one_paragraph(reports, baseline):
    parts = blocks(page(reports, holdout={101, 102}, baseline=baseline))
    assert len(parts) == 5
    assert parts[0].startswith("| Monitor, seeds |") and parts[1].startswith("At z 50 the plain ranking finds ")
    assert parts[2].startswith("The threshold was picked on the ")
    assert parts[3].startswith("| Threshold (z), seeds used while the brain was built |")
    assert parts[4].startswith("The plain ranking (`scripts/baseline_ranking.py`) ") and "\n" not in parts[4]
    assert all("\n" not in p for p in (parts[1], parts[2]))


def test_the_brain_and_the_plain_ranking_at_both_thresholds_are_set_side_by_side_on_the_summary_columns(reports, baseline):
    full = page(reports, holdout={101, 102}, baseline=baseline)
    summary = next(line for line in full.splitlines() if line.startswith("| Seeds |"))
    table = blocks(full)[0].splitlines()
    assert table[0] == summary.replace("| Seeds |", "| Monitor, seeds |") and table[1] == "|---|---:|---:|---:|---:|---:|---:|---:|"
    assert table[2:] == [
        "| Brain, holdout | 2 | 3/4 | 3/3 | 3/3 | 1 | 15/16 | 0.33 |",
        f"| Plain ranking at z 50 ({picked(0, 1)}), holdout | 2 | 3/4 | 2/3 | 3/3 | 1 | 16/16 | 0.33 |",
        f"| Plain ranking at z 12 ({SECOND}), holdout | 2 | 3/4 | 3/3 | 3/3 | 4 | 15/16 | 0.00 |",
        "| Brain, all runs | 3 | 6/7 | 5/6 | 5/6 | 1 | 23/24 | 0.50 |",
        f"| Plain ranking at z 50 ({picked(0, 1)}), all runs | 3 | 5/7 | 4/5 | 4/5 | 3 | 22/24 | 0.80 |",
        f"| Plain ranking at z 12 ({SECOND}), all runs | 3 | 6/7 | 6/6 | 5/6 | 7 | 20/24 | 0.17 |"]
    no_holdout = blocks(page(reports, baseline=baseline))[0]           # every seed is a built-on seed: 1 there
    assert [line.split(" |")[0] for line in no_holdout.splitlines()[2:]] == [
        "| Brain, all runs", f"| Plain ranking at z 50 ({picked(1, 1)}), all runs",
        f"| Plain ranking at z 12 ({SECOND}), all runs"]


def test_the_picked_threshold_is_labelled_with_counted_false_alarms_not_a_comparison_with_the_brain(reports, baseline):
    reports[0]["false_alarms"] = 2                                      # the brain on its built-on seed
    table = blocks(page(reports, holdout={101, 102}, baseline=baseline))[0]
    assert f"| Plain ranking at z 50 ({picked(2, 3)}), holdout |" in table and OLD_WORDING not in table
    quiet = dict(baseline, z=12.0, sweep=[sweep_row(8, 5), sweep_row(12, 3)])   # no threshold reached the bound
    assert ("| Plain ranking at z 12 (threshold picked for the fewest false alarms on the built-on seeds, 3; the brain: "
            "2 there, 3 in all runs), holdout |") in blocks(page(reports, holdout={101, 102}, baseline=quiet))[0]
    bare = dict(baseline, sweep=[])                                     # without a sweep the label claims no rule
    assert "| Plain ranking at z 50, holdout |" in blocks(page(reports, holdout={101, 102}, baseline=bare))[0]


def test_one_sentence_per_threshold_says_what_it_finds_by_fault_kind(reports, baseline):
    parts = blocks(page(reports, holdout={101, 102}, baseline=baseline))
    assert parts[1] == ("At z 50 the plain ranking finds 1/1 ad_budget_exhausted, 1/2 payment_failure and 1/1 "
                        "search_regression faults on the holdout. At z 12 it finds 1/1 ad_budget_exhausted, 1/2 "
                        "payment_failure and 1/1 search_regression faults on the holdout.")
    everywhere = blocks(page(reports, baseline=baseline))[1]            # without a holdout: all runs, from per_fault
    assert everywhere == ("At z 50 the plain ranking finds 1/1 ad_budget_exhausted, 1/3 payment_failure, 2/2 "
                          "search_regression and 1/1 stockout_surge faults in all runs. At z 12 it finds 1/1 "
                          "ad_budget_exhausted, 2/3 payment_failure, 2/2 search_regression and 1/1 stockout_surge faults "
                          "in all runs.")
    alone = dict(baseline, second=None)
    assert blocks(page(reports, holdout={101, 102}, baseline=alone))[1].count(". ") == 0


def test_the_built_on_sweep_is_listed_with_the_rule_that_picked_the_threshold(reports, baseline):
    parts = blocks(page(reports, holdout={101, 102}, baseline=baseline))
    assert parts[2] == ("The threshold was picked on the 1 runs of seeds used while the brain was built, never on the "
                        "holdout, by one rule: the smallest threshold tried with at most 1 false alarm there, else the one "
                        "with the fewest. It gives 50. The grid of thresholds tried (8, 12, 50) was fixed before the sweep "
                        "ran. The second point, 12, is the first version's threshold; both were fixed before this version "
                        "was run on the holdout.")
    assert [line for line in parts[3].splitlines() if re.match(r"\| \d", line)] == [
        "| 8 | 1 | 2/3 | 2/2 | 1/2 | 5 | 7/8 | 0.50 |", "| 12 (second point) | 1 | 2/3 | 2/2 | 1/2 | 3 | 7/8 | 0.50 |",
        "| 50 (picked) | 1 | 2/3 | 2/2 | 1/2 | 1 | 7/8 | 0.50 |"]
    none = dict(baseline, z=12.0, sweep=[sweep_row(8, 5), sweep_row(12, 3)])
    assert ("None tried had at most 1 false alarm there, so it gives 12, the one with the fewest. The grid of thresholds "
            "tried (8, 12) was fixed before the sweep ran.") in blocks(page(reports, baseline=none))[2]
    assert blocks(page(reports, baseline=baseline))[2].endswith("The second point, 12, is the first version's threshold.")


RULE = ("Its cause, always written as supported, is the latest release whose country and platform do not contradict the "
        "place (any country when the place names none), from 12 hours before to 2 hours after the first alert; a rollback "
        "is never named; otherwise the metric that moved.")
FIRST = ("The first version gave a history that never varied a spread of 1e-6 and had been run on the holdout: it found "
         "94/120 at z 12, this version 3/4; it named the right cause for 73, raised 396 false alarms (323 of them from "
         "never-varied histories) and stayed silent through 33/80 innocent events. Its floor was removed after a review of "
         "those alarms, and the threshold was then picked on the built-on seeds only.")


def test_the_paragraph_says_what_the_plain_ranking_is_what_it_does_not_use_and_how_it_names_a_cause(reports, baseline):
    paragraph = blocks(page(reports, holdout={101, 102}, baseline=baseline))[4]
    assert "It watches the same rates in the same cuts as the brain and judges each hour on its own" in paragraph
    assert ("It does not use the calendar, sums over three hours, the targeted test after each release, the search over "
            "all places or the investigation of causes.") in paragraph
    assert paragraph.endswith(" " + RULE + " " + FIRST)       # no cause is unconfirmed on this holdout: no count of them
    assert "only helps" not in paragraph
    other = dict(baseline, first_version=dict(baseline["first_version"], false_alarms=7, never_varied=5))
    assert "raised 7 false alarms (5 of them from" in blocks(page(reports, baseline=other))[4]   # from the data given


def test_the_first_version_is_set_beside_this_version_at_its_threshold_on_the_holdout_only(reports, baseline):
    nothing_to_compare = blocks(page(reports, baseline=baseline))[4]           # no holdout on the page
    assert ("had been run on the holdout: it found 94/120 at z 12; it named the right cause for 73,") in nothing_to_compare
    at_picked = dict(baseline, z=12.0, second=None)                            # this version at 12 is the picked point
    assert "it found 94/120 at z 12, this version 3/4;" in blocks(page(reports, holdout={101, 102}, baseline=at_picked))[4]
    elsewhere = dict(baseline, second=dict(z=10.0, runs=baseline["second"]["runs"]))   # nothing of this version at 12
    assert "it found 94/120 at z 12; it named" in blocks(page(reports, holdout={101, 102}, baseline=elsewhere))[4]


def test_the_paragraph_counts_the_unconfirmed_causes_that_are_stock_outs_in_a_category(reports, baseline):
    wrong = lambda kind, **scope: fault(kind, cause=False, supported=1, **scope)   # noqa: E731
    low = [run(101, [wrong("stockout_surge", category="beauty"), wrong("stockout_surge", category="sports"),
                     wrong("payment_failure", country="FR", platform="iOS"),
                     wrong("stockout_surge", country="DE", category="toys"),     # a country: only its releases are named
                     fault("stockout_surge", category="home"), fault("search_regression", detected=False, country="ES")]),
           run(4, [wrong("stockout_surge", category="fashion")])]                  # a built-on seed: not on the holdout
    high = [run(101, [wrong("stockout_surge", category="beauty"), fault("payment_failure", country="FR", platform="iOS")]),
            run(4, [])]
    data = dict(baseline, runs=low, second=dict(z=12.0, runs=high))
    assert se.unconfirmed(low[:1]) == (2, 4) and se.unconfirmed(low) == (3, 5)
    paragraph = blocks(page(reports, holdout={101, 102}, baseline=data))[4]
    counted = ("Of its 4 unconfirmed causes on the holdout at z 50, 2 are stock-outs in a category, where the rule names "
               "whatever release falls in the window; at z 12, 1 of 1.")
    assert RULE + " " + counted + " The first version " in paragraph
    assert ("Of its 5 unconfirmed causes in all runs at z 50, 3 are stock-outs in a category, where the rule names whatever "
            "release falls in the window; at z 12, 1 of 1.") in blocks(page(reports, baseline=data))[4]
    only_high = dict(data, runs=[run(101, [fault("stockout_surge", category="beauty")])])
    assert ("Of its 1 unconfirmed cause on the holdout at z 12, 1 is a stock-out in a category, where the rule names "
            "whatever release falls in the window.") in blocks(page(reports, holdout={101, 102}, baseline=only_high))[4]


def test_the_readme_quotes_the_tracked_holdout_numbers_in_one_sentence():
    """The README's paragraph on the plain ranking ("At a glance") says what docs/results.json gives, counted the page's way."""
    readme, tracked = ((ROOT / name).read_text(encoding="utf-8") for name in ("README.md", "docs/results.md"))
    assert OLD_WORDING not in readme and OLD_WORDING not in tracked
    saved = json.loads((ROOT / "docs" / "results.json").read_text(encoding="utf-8"))
    holdout, b = set(saved["holdout"]), saved["baseline"]
    fresh = lambda rs: [r for r in rs if r["seed"] in holdout]          # noqa: E731
    brain, low, high = (se.tally(fresh(rs)) for rs in (saved["runs"], b["runs"], b["second"]["runs"]))
    built = se.tally([r for r in saved["runs"] if r["seed"] not in holdout])
    stockouts, unconfirmed = se.unconfirmed(fresh(b["runs"]))
    line = next(text for text in readme.splitlines() if text.startswith("**Against the plain ranking**"))
    lead, numbers = line.split("`--baseline`). ")
    assert len(holdout) == 10 and numbers.endswith(".") and ". " not in numbers          # one sentence
    assert numbers.startswith(
        f"On the ten holdout seeds it finds {low['detected']} of {low['faults']} faults, names the right cause for "
        f"{low['correct']}, raises {low['false_alarms']} false alarm{'' if low['false_alarms'] == 1 else 's'} and stays silent through {low['silent']} of "
        f"{low['innocents']} innocent events at z {b['z']:g}, the threshold picked for at most {se.MAX_FALSE_ALARMS} false "
        f"alarm on the built-on seeds (the brain: {built['false_alarms']} there, {se.tally(saved['runs'])['false_alarms']} "
        f"in all runs), and at z {b['second']['z']:g} it finds {high['detected']}, names the right cause for "
        f"{high['correct']}, raises {high['false_alarms']} false alarm{'' if high['false_alarms'] == 1 else 's'} and stays silent through {high['silent']} of "
        f"{high['innocents']}, against {brain['detected']}, {brain['correct']}, {brain['false_alarms']} and "
        f"{brain['silent']} for the brain: ")
    assert (f"part of the cause gap is the plain ranking's own cause rule: {stockouts} of its {unconfirmed} unconfirmed "
            f"causes at z {b['z']:g} are stock-outs in a category, where that rule names whatever release falls in the "
            "window.") in numbers


@pytest.mark.parametrize("false_alarms, chosen", [
    ({3: 10, 4: 1, 5: 0}, (4, True)),          # the smallest within the bound, not the quietest
    ({3: 5, 4: 2, 5: 2}, (4, False)),          # none within: the fewest, the smaller on a tie
    ({12: 0}, (12, True)),
])
def test_the_threshold_rule(false_alarms, chosen):
    assert se.pick_threshold([sweep_row(z, n) for z, n in false_alarms.items()]) == chosen


def test_the_page_with_the_plain_ranking_is_rebuilt_from_its_json(reports, baseline, tmp_path):
    out = tmp_path / "results.md"
    meta = dict(holdout=[101, 102], commit="abc1234", warmup_hours=80, baseline=baseline)
    se.write(out, se.render(reports, **meta), reports, meta)
    saved = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert list(saved) == ["holdout", "commit", "warmup_hours", "runs", "baseline"]
    assert sorted(saved["baseline"]) == ["first_version", "runs", "second", "sweep", "z"] and saved["baseline"]["z"] == 50
    assert se.from_json(out.with_suffix(".json")) == out.read_text(encoding="utf-8").splitlines()
    plain = tmp_path / "plain.md"
    se.write(plain, se.render(reports), reports)
    assert "baseline" not in json.loads(plain.with_suffix(".json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("args, message", [
    (["--seeds", "4", "--baseline"], "--baseline needs --baseline-z"),
    (["--seeds", "4", "--baseline-z", "8"], "--baseline-z is given without --baseline"),
    (["--seeds", "4", "--baseline", "--baseline-z", "0"], "threshold must be a positive number"),
    (["--seeds", "4", "--baseline", "--baseline-z", "nan"], "threshold must be a positive number"),
])
def test_plain_ranking_arguments_that_cannot_give_a_page_are_refused(tmp_path, capsys, args, message):
    out = tmp_path / "r.md"
    with pytest.raises(SystemExit) as stop:
        se.main(["--runs", str(tmp_path), "--out", str(out)] + args)
    assert stop.value.code == 2 and message in capsys.readouterr().err
    assert not out.exists() and not list(tmp_path.glob("*.json"))


def _incidents(folder, faults, stray=0, threshold=None):
    """Made-up incident files for the faults (and stray ones on clean days); with a threshold, as the plain ranking
    writes them: the threshold in every file and in threshold.txt."""
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    bodies = [{"id": f"x-{n}", "first_seen": (f.start + pd.Timedelta(hours=1)).strftime("%Y-%m-%dT%H:00:00Z"),
               "segment": {d: f.scope[d] for d in sc.DIMS if d in f.scope}, "hypotheses": []} for n, f in enumerate(faults)]
    bodies += [{"id": f"stray-{n}", "first_seen": (faults[0].start - pd.Timedelta(days=5 + n)).strftime("%Y-%m-%dT%H:00:00Z"),
                "segment": {"country": "PL"}, "metric": "order_rate", "status": "resolved", "hypotheses": []}
               for n in range(stray)]
    for body in bodies:
        if threshold is not None:
            body["threshold"] = float(threshold)
        (folder / f"{body['id']}.json").write_text(json.dumps(body), encoding="utf-8")
    if threshold is not None:
        (folder / "threshold.txt").write_text(f"{float(threshold)!r}\n", encoding="utf-8")


def test_a_plain_ranking_folder_is_read_only_at_the_threshold_it_recorded(tmp_path):
    events, _, _ = sc.load_truth(sc.generate(tmp_path / "seed3.db", faults=4, innocents=4, seed=3))
    faults = [e for e in events if e.kind in sc.FAULT_KINDS]
    folder = tmp_path / "seed3-baseline"
    _incidents(folder, faults[:2], threshold=50)
    assert sorted(i["id"] for i in se.plain_incidents(folder, 50)) == ["x-0", "x-1"]
    with pytest.raises(ValueError, match="seed3-baseline: recorded threshold 50, not 12"):
        se.plain_incidents(folder, 12)
    _incidents(folder, [], threshold=50)                                  # a run that raised nothing: a measured zero
    assert se.plain_incidents(folder, 50) == []
    (folder / "threshold.txt").unlink()                                   # without its record it is no finished run
    with pytest.raises(ValueError, match="no threshold.txt"):
        se.plain_incidents(folder, 50)
    _incidents(folder, faults[:2], threshold=50)
    body = json.loads((folder / "x-1.json").read_text(encoding="utf-8"))
    del body["threshold"]
    (folder / "x-1.json").write_text(json.dumps(body), encoding="utf-8")   # one file that does not say its threshold
    with pytest.raises(ValueError, match="recorded threshold 50, none, not 50"):
        se.plain_incidents(folder, 50)


def test_the_plain_ranking_and_its_sweep_are_scored_from_their_own_folders(tmp_path, capsys):
    runs = tmp_path / "runs"
    faults = {}
    for seed in (3, 5):
        events, _, _ = sc.load_truth(sc.generate(runs / f"seed{seed}.db", faults=4, innocents=4, seed=seed))
        faults[seed] = [e for e in events if e.kind in sc.FAULT_KINDS]
        _incidents(runs / f"seed{seed}-incidents", faults[seed])
        _incidents(runs / f"seed{seed}-baseline", faults[seed][:2], threshold=8)
    _incidents(runs / "seed3-baseline-z12-all", faults[3], stray=1, threshold=12)
    _incidents(runs / "seed5-baseline-z12-all", faults[5][:3], threshold=12)
    _incidents(runs / "seed3-baseline-z4", faults[3], stray=2, threshold=4)     # the sweep: seed 3 only, not a holdout
    _incidents(runs / "seed3-baseline-z8", faults[3][:3], threshold=8)
    _incidents(runs / "seed3-baseline-z12", [], threshold=12)                   # it raised nothing at 12
    out = tmp_path / "results.md"
    args = ["--runs", str(runs), "--seeds", "3,5", "--holdout", "5", "--commit", "abc1234", "--warmup-hours", "80",
            "--out", str(out), "--baseline"]
    assert se.main(args + ["--baseline-z", "8"]) == 0
    text = out.read_text(encoding="utf-8")
    table, kinds, rule, sweep, _ = blocks(text)
    assert f"| Plain ranking at z 8 ({picked(0, 0)}), holdout | 1 | 2/4 | 2/2 | 0/2 | 0 |" in table
    assert f"| Plain ranking at z 8 ({picked(0, 0)}), all runs | 2 | 4/8 | 4/4 | 0/4 | 0 |" in table
    assert f"| Plain ranking at z 12 ({SECOND}), holdout | 1 | 3/4 | 3/3 | 0/3 | 0 |" in table
    assert f"| Plain ranking at z 12 ({SECOND}), all runs | 2 | 7/8 | 7/7 | 0/7 | 1 |" in table
    low, high = kinds.split(". At z 12 it finds ")
    assert low.startswith("At z 8 the plain ranking finds ") and high.endswith(" faults on the holdout.")
    for sentence, found in ((low, 2), (high, 3)):                       # the holdout seed's four faults, by kind
        counts = [tuple(map(int, c)) for c in re.findall(r"(\d+)/(\d+) [a-z_]+", sentence)]
        assert (sum(c[0] for c in counts), sum(c[1] for c in counts)) == (found, 4)
    assert "| 4 | 1 | 4/4 | 4/4 | 0/4 | 2 |" in sweep and "| 8 (picked) | 1 | 3/4 | 3/3 | 0/3 | 0 |" in sweep
    assert "| 12 (second point) | 1 | 0/4 | 0/0 | 0/0 | 0 | 4/4 | n/a |" in sweep and "It gives 8." in rule
    assert "The grid of thresholds tried (4, 8, 12) was fixed before the sweep ran." in rule
    saved = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))["baseline"]
    assert saved["z"] == 8 and [s["z"] for s in saved["sweep"]] == [4, 8, 12] and saved["second"]["z"] == 12
    assert [r["seed"] for r in saved["runs"]] == [3, 5] and [r["seed"] for r in saved["second"]["runs"]] == [3, 5]
    assert saved["first_version"] == se.FIRST_VERSION
    assert se.from_json(out.with_suffix(".json")) == text.splitlines()
    before = out.read_bytes()

    def refused(message, z="8"):
        with pytest.raises(SystemExit) as stop:
            se.main(args + ["--baseline-z", z])
        assert stop.value.code == 2 and message in capsys.readouterr().err
        assert out.read_bytes() == before

    refused("--baseline-z 4 is not the threshold the sweep gives (8)", z="4")          # a threshold the rule does not give
    _incidents(runs / "seed5-baseline", faults[5][:2], threshold=12)                   # run at another threshold
    refused("seed5-baseline: recorded threshold 12, not 8")
    _incidents(runs / "seed5-baseline", faults[5][:2], threshold=8)
    _incidents(runs / "seed3-baseline-z12-all", faults[3], threshold=10)               # the second point at another one
    refused("seed3-baseline-z12-all: recorded threshold 10, not 12")
    _incidents(runs / "seed3-baseline-z12-all", faults[3], stray=1, threshold=12)
    _incidents(runs / "seed3-baseline-z4", faults[3], stray=2, threshold=5)            # a sweep folder misnamed
    refused("seed3-baseline-z4: recorded threshold 5, not 4")
    _incidents(runs / "seed3-baseline-z4", faults[3], stray=2)                         # or not written by the ranking
    refused("seed3-baseline-z4: no threshold.txt")
    _incidents(runs / "seed3-baseline-z4", faults[3], stray=2, threshold=4)
    (runs / "seed5-baseline-z8").mkdir()                                               # a sweep on the holdout
    refused("holdout seed 5 has threshold-sweep folders")
    (runs / "seed5-baseline-z8").rmdir()
    assert se.main(args + ["--baseline-z", "8"]) == 0 and out.read_bytes() == before   # restored, the same page


def shifted(obj, by=10):
    """The same runs with seeds 101 and 102 renamed 111 and 112, everywhere they appear."""
    if isinstance(obj, dict):
        return {k: (v + by if k == "seed" and v in (101, 102) else shifted(v, by)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [shifted(x, by) for x in obj]
    return obj


def test_on_a_later_holdout_the_first_version_is_named_as_the_first_holdout_s_and_not_compared(reports, baseline):
    at_picked = shifted(dict(baseline, z=12.0, second=None))
    paragraph = blocks(page(shifted(reports), holdout={111, 112}, baseline=at_picked))[4]
    assert "had been run on the first holdout (seeds 101 to 110, not this page's holdout): it found 94/120 at z 12;" in paragraph
    assert "this version" not in paragraph.split("The first version")[1]
