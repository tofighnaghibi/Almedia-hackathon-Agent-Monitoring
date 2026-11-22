"""The plain-ranking baseline (scripts/baseline_ranking.py): what an analyst builds in an afternoon, kept in the
scoring harness so the brain has something honest to beat (README 3.5). It reads the facts and the releases
and nothing else, judges each hour only from the hours before it, and writes incident files the scorer reads."""
import json
import shutil
import sqlite3

import numpy as np
import pandas as pd
import pytest

from scripts import baseline_ranking as br
from sim import db, inject
from sim import score as sc
from sim.run import DEFAULT_START, backfill

DAYS = 17
START = pd.Timestamp(DEFAULT_START)
FAULT = START + pd.Timedelta(days=15, hours=19)     # 20:00 local, the evening peak; 15 days of history at that hour
HOUR = pd.Timedelta(hours=1)


def iso(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%dT%H:00:00Z")


@pytest.fixture(scope="module")
def clean_db(tmp_path_factory):
    return backfill(tmp_path_factory.mktemp("clean") / "clean.db", DAYS * 24, countries=("DE", "FR"), add_context=True)


@pytest.fixture(scope="module")
def fault_db(tmp_path_factory):
    """A payment fault in DE x Android for four hours, with its release, and three releases that must not be named."""
    path = backfill(tmp_path_factory.mktemp("fault") / "fault.db", DAYS * 24, countries=("DE", "FR"))
    con = db.connect(path)
    try:
        con.executemany("INSERT INTO releases VALUES (?, ?, ?, ?, ?)", [
            (iso(FAULT - 13 * HOUR), "payments-sdk", "DE", "Android", "payments-sdk 4.1 rollout (DE, Android)"),
            (iso(FAULT + HOUR), "search", "DE", "iOS", "search rollout (DE, iOS)"),
            (iso(FAULT + HOUR), "pacing", "FR", "Android", "pacing rollout (FR, Android)"),
        ])
        con.commit()
        inject.payment_failure(con, "DE", "Android", FAULT, severity=0.6, end=FAULT + 4 * HOUR)
    finally:
        con.close()
    return path


# ------------------------------------------------------------------------------------ what it raises
def test_a_clean_history_raises_nothing_at_a_high_threshold(clean_db):
    s = br.scores(br.read_tables(clean_db)[0])
    assert np.abs(s.z[np.isfinite(s.z)]).max() < 10
    assert br.run(clean_db, z=10) == []
    assert br.run(clean_db, z=3), "noise must show at a low threshold"


def test_a_cell_hour_whose_history_never_varied_is_not_judged(clean_db):
    """A history with no spread has nothing to measure a change against. The first version gave it a floor of 1e-6,
    and one lost order then scored a z in the tens of thousands at any threshold; now such a cell-hour is not judged.
    Rounding leaves a spread of about 1e-17 on a history of equal rates (0.1 is not exact), so below 1e-12 is none."""
    facts = br.read_tables(clean_db)[0]
    facts["payment_attempts"] = 10 * facts["payment_successes"]          # every payment success rate exactly 0.1
    facts["orders"] = facts["payment_successes"]                         # every order rate exactly 1
    at = (facts["ts"] == iso(FAULT)) & (facts["country"] == "DE") & (facts["platform"] == "Android")
    row = facts.loc[at, "orders"].idxmax()
    assert facts.loc[row, "orders"] >= 3
    facts.loc[row, ["payment_attempts", "orders"]] += [5, -3]            # then, at one hour, both move in one row
    s = br.scores(facts)
    h = int((FAULT - s.hours[0]) / HOUR)                                  # up to FAULT (later, FAULT is in the history)
    for metric in ("payment_success_rate", "order_rate"):
        m = s.metrics.index(metric)
        spread = s.spread[m, :h + 1][np.isfinite(s.spread[m, :h + 1])]
        assert spread.size and spread.max() < 1e-12                       # their history never varied
        assert (s.rate[m, h] < s.expected[m, h]).sum() >= 4               # they moved at FAULT, in every cell of the row
        assert np.isnan(s.z[m, :h + 1]).all()                             # and are not judged
    assert np.isfinite(s.z[s.metrics.index("search_ctr"), :h + 1]).any()  # the other rates still are
    assert not [p for p in br.rank(s, z=3) if p.metric in ("payment_success_rate", "order_rate") and p.hour <= FAULT]


def test_every_incident_records_its_threshold(fault_db, tmp_path):
    assert {i["threshold"] for i in br.run(fault_db, z=3)} == {3.0}
    out = tmp_path / "baseline"
    assert br.main(["--db", str(fault_db), "--out", str(out), "--z", "8"]) == 0
    files = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(out.glob("base-*.json"))]
    assert files and all(i["threshold"] == 8.0 for i in files)
    assert (out / "threshold.txt").read_bytes() == b"8.0\n"


def test_a_run_that_raises_nothing_still_records_its_threshold(fault_db, tmp_path):
    """A folder with no incident files is a measured zero only when the run left its record."""
    out = tmp_path / "quiet"
    assert br.main(["--db", str(fault_db), "--out", str(out), "--z", "1e9"]) == 0
    assert [p.name for p in out.iterdir()] == ["threshold.txt"]
    assert float((out / "threshold.txt").read_text(encoding="utf-8")) == 1e9
    assert br.main(["--db", str(fault_db), "--out", str(out), "--z", "8"]) == 0          # its own record is replaced
    assert (out / "threshold.txt").read_text(encoding="utf-8") == "8.0\n" and len(list(out.iterdir())) > 1


def test_a_strong_payment_fault_is_found_in_its_first_hour_in_its_place(fault_db):
    found = [i for i in br.run(fault_db, z=8) if FAULT <= pd.Timestamp(i["first_seen"]) < FAULT + 4 * HOUR]
    assert len(found) == 1
    i = found[0]
    assert i["first_seen"] == iso(FAULT) and i["last_seen"] == iso(FAULT + 3 * HOUR)
    assert i["metric"] == "payment_success_rate" and i["segment"] == {"country": "DE", "platform": "Android"}
    assert i["drivers"][0]["metric"] == "payment_success_rate" and i["drivers"][0]["z"] < -8
    assert i["hypotheses"] == [{"cause": "release payments-sdk: payments-sdk 4.2 rollout (DE, Android)",
                                "kind": "release", "status": "supported"}]
    assert i["status"] == "resolved" and i["id"].startswith("base-") and i["report"].count(". ") == 0


def test_the_files_are_what_the_scorer_reads_and_score_the_fault(fault_db, tmp_path):
    out = tmp_path / "baseline"
    assert br.main(["--db", str(fault_db), "--out", str(out), "--z", "8"]) == 0
    assert b"\r\n" not in b"".join(p.read_bytes() for p in out.iterdir())
    events, start, end = sc.load_truth(fault_db)
    r = sc.score(events, sc.load_incidents(out), end, (end - start + HOUR) / pd.Timedelta(days=1))
    [p] = r["per_fault"]
    assert p["detected"] and p["delay_h"] == 0 and p["localized"] and p["correct_cause"]


# ------------------------------------------------------------------------------------ the rules, one by one
RELEASES = pd.DataFrame([
    ("2026-11-09T23:00:00Z", "payments-sdk", "DE", "Android", "payments-sdk 4.1 rollout (DE, Android)"),   # 13 h before
    ("2026-11-10T00:00:00Z", "payments-sdk", "DE", "Android", "payments-sdk 4.2 rollout (DE, Android)"),   # 12 h before
    ("2026-11-10T10:00:00Z", "search", "DE", None, "search ranking v2 rollout (DE)"),                       # no platform
    ("2026-11-10T12:00:00Z", "pacing", "FR", "Android", "pacing rollout (FR, Android)"),                   # another country
    ("2026-11-10T13:00:00Z", "search", "DE", "iOS", "search rollout (DE, iOS)"),                           # another platform
    ("2026-11-10T14:00:00Z", "payments-sdk", "DE", "Android", "payments-sdk 4.2 rollback (DE, Android)"),  # a rollback
    ("2026-11-10T15:00:00Z", "payments-sdk", "DE", "Android", "payments-sdk 4.3 rollout (DE, Android)"),   # 3 h after
], columns=["ts", "system", "country", "platform", "description"])
AT = pd.Timestamp("2026-11-10T12:00:00Z")


def named(rows, place, at=AT):
    hit = br.pick_release(rows, place, at)
    return None if hit is None else hit["description"]


def test_the_release_is_the_latest_one_in_the_place_from_12_hours_before_to_2_hours_after():
    place = {"country": "DE", "platform": "Android"}
    assert named(RELEASES, place) == "search ranking v2 rollout (DE)"            # an empty platform matches any
    assert named(RELEASES.drop(index=2), place) == "payments-sdk 4.2 rollout (DE, Android)"
    assert named(RELEASES.drop(index=[1, 2]), place) is None                      # 13 h before is too early
    assert named(RELEASES, {"country": "DE", "platform": "Web"}) == "search ranking v2 rollout (DE)"
    assert named(RELEASES, {"country": "DE"}) == "search rollout (DE, iOS)"      # no platform in the place: any
    assert named(RELEASES, {"country": "FR", "platform": "Android"}) == "pacing rollout (FR, Android)"
    assert named(RELEASES, {"country": "PL"}) is None
    assert named(RELEASES, {"category": "sports"}) == "search rollout (DE, iOS)"  # no country in the place: any
    # 4.3 comes 3 h after AT, so it was left out above; an hour later it is exactly 2 h after and counts
    assert named(RELEASES, {"country": "DE", "platform": "Android"}, AT + HOUR) == "payments-sdk 4.3 rollout (DE, Android)"


def test_the_cause_is_the_release_text_or_else_the_metric_that_moved():
    rel = br.cause(RELEASES, {"country": "DE", "platform": "Android"}, AT, "payment_success_rate", -12.0)
    assert rel == {"cause": "release search: search ranking v2 rollout (DE)", "kind": "release", "status": "supported"}
    met = br.cause(RELEASES, {"country": "PL", "category": "sports"}, AT, "stockout_rate", 30.0)
    assert met == {"cause": "stockout_rate rose in PL x sports", "kind": "metric", "status": "supported"}
    whole = br.cause(RELEASES.iloc[:0], {}, AT, "ad_load", -9.0)
    assert whole["cause"] == "ad_load fell in the whole market"


def pick(hour, metric, z, **place):
    return br.Pick(hour=AT + hour * HOUR, metric=metric, place=place, z=z, rate=0.5, expected=0.6)


def test_alerts_join_an_incident_of_the_same_metric_in_a_nested_place_that_alerted_at_most_2_hours_before():
    picks = [pick(0, "payment_success_rate", -20, country="DE", platform="Android"),
             pick(1, "payment_success_rate", -15, country="DE"),                     # wider place: joins
             pick(3, "payment_success_rate", -18, country="DE", platform="Android"),  # 2 h after the last: joins
             pick(6, "payment_success_rate", -18, country="DE", platform="Android"),  # 3 h after: a new incident
             pick(7, "payment_success_rate", -18, country="DE", platform="iOS"),      # not nested: a new incident
             pick(8, "checkout_rate", -18, country="DE", platform="iOS")]             # another metric: a new incident
    incs = br.incidents(picks, RELEASES.iloc[:0])
    assert [(i["id"], i["first_seen"], i["last_seen"]) for i in incs] == [
        ("base-0001", iso(AT), iso(AT + 3 * HOUR)), ("base-0002", iso(AT + 6 * HOUR), iso(AT + 6 * HOUR)),
        ("base-0003", iso(AT + 7 * HOUR), iso(AT + 7 * HOUR)), ("base-0004", iso(AT + 8 * HOUR), iso(AT + 8 * HOUR))]
    assert incs[0]["segment"] == {"country": "DE", "platform": "Android"}        # the place of the first alert


def test_each_hour_keeps_only_the_largest_deviation_and_on_a_tie_the_finer_cell():
    cells = [{}, {"country": "DE"}, {"country": "DE", "platform": "iOS"}]
    z = np.array([[[2.0, 5.0, -5.0], [9.0, np.nan, 4.0], [1.0, 1.0, 1.0]],      # metric a, three hours
                  [[np.nan, 6.0, 1.0], [-9.5, 3.0, 2.0], [2.0, 2.0, 2.0]]])     # metric b
    scores = br.Scores(hours=pd.date_range(AT, periods=3, freq="h"), metrics=["a", "b"], cells=cells,
                       rate=np.ones_like(z), expected=np.ones_like(z), z=z)
    picks = br.rank(scores, z=4)
    assert [(p.hour, p.metric, p.place, p.z) for p in picks] == [
        (AT, "b", {"country": "DE"}, 6.0), (AT + HOUR, "b", {}, -9.5)]              # the third hour stays below 4
    tie = br.rank(br.Scores(hours=scores.hours[:1], metrics=["a"], cells=cells, rate=np.ones((1, 1, 3)),
                            expected=np.ones((1, 1, 3)), z=np.array([[[7.0, 7.0, -7.0]]])), z=4)
    assert tie[0].place == {"country": "DE", "platform": "iOS"} and tie[0].z == -7.0


# ------------------------------------------------------------------------------------ what it may look at
def test_the_expectation_is_causal(fault_db, tmp_path):
    """Rewriting every hour after t changes nothing at t or before."""
    t = FAULT + HOUR
    changed = tmp_path / "changed.db"
    shutil.copy(fault_db, changed)
    con = sqlite3.connect(changed)
    try:
        con.execute("UPDATE facts_hourly SET payment_successes = 0, search_clicks = searches, stockouts = product_views "
                    "WHERE ts > ?", (iso(t),))
        con.commit()
    finally:
        con.close()
    a, b = (br.scores(br.read_tables(p)[0]) for p in (fault_db, changed))
    upto = int((t - a.hours[0]) / HOUR) + 1
    np.testing.assert_array_equal(a.z[:, :upto], b.z[:, :upto])
    np.testing.assert_array_equal(a.expected[:, :upto], b.expected[:, :upto])
    assert not np.array_equal(a.z[:, upto:], b.z[:, upto:], equal_nan=True)     # the later hours did change
    before = lambda s: [(p.hour, p.metric, p.place, p.z) for p in br.rank(s, z=8) if p.hour <= t]   # noqa: E731
    assert before(a) == before(b) and before(a)


def test_the_answer_key_and_the_other_context_tables_are_never_read(fault_db, tmp_path):
    blind = tmp_path / "blind.db"
    shutil.copy(fault_db, blind)
    con = sqlite3.connect(blind)
    try:
        for table in ("ground_truth", "sim_meta", "calendar", "ingestion_log"):
            con.execute(f"DROP TABLE {table}")
        con.commit()
    finally:
        con.close()
    a, b = tmp_path / "a", tmp_path / "b"
    assert br.main(["--db", str(fault_db), "--out", str(a), "--z", "8"]) == 0
    assert br.main(["--db", str(blind), "--out", str(b), "--z", "8"]) == 0
    names = sorted(p.name for p in a.iterdir())
    assert names and names == sorted(p.name for p in b.iterdir())
    assert all((a / n).read_bytes() == (b / n).read_bytes() for n in names)


def test_the_database_is_opened_read_only_and_left_without_side_files(fault_db, tmp_path):
    copy = tmp_path / "copy.db"
    shutil.copy(fault_db, copy)
    before = copy.read_bytes()
    br.read_tables(copy)
    assert copy.read_bytes() == before and sorted(p.name for p in tmp_path.iterdir()) == ["copy.db"]


# ------------------------------------------------------------------------------------ the output folder
def test_out_refuses_a_folder_with_files_it_did_not_write(fault_db, tmp_path, capsys):
    folder = tmp_path / "seed1-incidents"
    folder.mkdir()
    (folder / "inc-0001.json").write_text("{}", encoding="utf-8")
    (folder / "base-0001.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as stop:
        br.main(["--db", str(fault_db), "--out", str(folder), "--z", "8"])
    assert stop.value.code == 2 and "inc-0001.json" in capsys.readouterr().err
    assert sorted(p.name for p in folder.iterdir()) == ["base-0001.json", "inc-0001.json"]   # nothing was touched
    (folder / "inc-0001.json").unlink()
    (folder / "notes").mkdir()
    with pytest.raises(SystemExit):
        br.main(["--db", str(fault_db), "--out", str(folder), "--z", "8"])
    assert (folder / "base-0001.json").read_text(encoding="utf-8") == "{}"


def test_out_is_emptied_of_its_own_earlier_files(fault_db, tmp_path):
    folder = tmp_path / "baseline"
    folder.mkdir()
    (folder / "base-0099.json").write_text("{}", encoding="utf-8")
    assert br.main(["--db", str(fault_db), "--out", str(folder), "--z", "8"]) == 0
    names = sorted(p.name for p in folder.iterdir())
    assert "base-0099.json" not in names and names[0] == "base-0001.json"
    assert json.loads((folder / "base-0001.json").read_text(encoding="utf-8"))["id"] == "base-0001"


@pytest.mark.parametrize("args, message", [
    (["--z", "0"], "threshold must be a positive number"),
    (["--z", "nan"], "threshold must be a positive number"),
    (["--z", "8", "--db", "missing.db"], "no database"),
])
def test_arguments_that_cannot_give_a_run_are_refused(fault_db, tmp_path, capsys, args, message):
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as stop:
        br.main(["--db", str(fault_db), "--out", str(out)] + [str(tmp_path / a) if a == "missing.db" else a for a in args])
    assert stop.value.code == 2 and message in capsys.readouterr().err and not out.exists()
