"""The scorer and the generator for the newer simulator kinds (traffic_drop, feed_unavailable, discount_push faults and
the innocent mix_shift), the opt-in --kinds extended and --release-mode options, and proof that the default generator
still writes exactly what it wrote before those options existed."""
import hashlib
import re
import sqlite3

import pandas as pd
import pytest

from sim import db, inject
from sim import score as sc
from sim.run import backfill

# Digest of generate(faults=4, innocents=2, seed=4, days=25) with the code of main 075b2be, before the new kinds and
# options were added. The full proof (seed 4, 49 days, against D:/hacklab2/eval4/seed4.db) is in the progress notes.
DEFAULT_DIGEST = {
    "facts_hourly": (108000, "8da1888dfa127ff3"),
    "ground_truth": (6, "69b498cbe803b1cb"),
    "releases": (28, "9f2f0955e97c6c63"),
    "calendar": (3, "8c8c64f83a240005"),
    "ingestion_log": (4200, "1afba4a27684def3"),
}
QUERIES = {
    "facts_hourly": "SELECT * FROM facts_hourly ORDER BY ts, country, platform, category, channel",
    "ground_truth": "SELECT injected_at, kind, scope, description FROM ground_truth ORDER BY rowid",
    "releases": "SELECT * FROM releases ORDER BY rowid",
    "calendar": "SELECT * FROM calendar ORDER BY rowid",
    "ingestion_log": "SELECT * FROM ingestion_log ORDER BY source, ts",
}


def digest(path):
    con = sqlite3.connect(path)
    try:
        out = {}
        for name, q in QUERIES.items():
            h, n = hashlib.sha256(), 0
            for row in con.execute(q):
                h.update(repr(row).encode())
                n += 1
            out[name] = (n, h.hexdigest()[:16])
        return out
    finally:
        con.close()


def rows(path, sql, args=()):
    con = sqlite3.connect(path)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def fmt(ts):
    return ts.strftime("%Y-%m-%dT%H:00:00Z")


# ----------------------------------------------------------------------------- the default stays bit-identical
def test_the_default_generator_writes_exactly_what_it_wrote_before(tmp_path):
    assert digest(sc.generate(tmp_path / "d.db", faults=4, innocents=2, seed=4, days=25)) == DEFAULT_DIGEST
    explicit = sc.generate(tmp_path / "e.db", faults=4, innocents=2, seed=4, days=25, kinds="core", release_mode="exact")
    assert digest(explicit) == DEFAULT_DIGEST


# ----------------------------------------------------------------------------- the opt-in generator
@pytest.fixture(scope="module")
def extended(tmp_path_factory):
    path = sc.generate(tmp_path_factory.mktemp("ext") / "ext.db", faults=7, innocents=3, seed=5, days=35, kinds="extended")
    events, start, end = sc.load_truth(path)
    return path, events, start, end, (end - start) / pd.Timedelta(days=1)


def test_extended_generation_has_every_kind_once_and_events_do_not_overlap(extended):
    path, events, start, end, _ = extended
    kinds = sorted(e.kind for e in events)
    assert kinds == sorted(sc.ALL_FAULT_KINDS + ("promo", "data_delay", "mix_shift"))
    ordered = sorted(events, key=lambda e: e.start)
    assert all(a.end <= b.start for a, b in zip(ordered, ordered[1:]))
    assert ordered[0].start >= start + pd.Timedelta(days=21) and all(e.end <= end for e in events)
    recs = rows(path, "SELECT description FROM releases WHERE system = 'recommendations' ORDER BY ts")
    assert recs == [("recommendations model 3.1 rollout",), ("recommendations ranking 3.2 rollout",)] or \
        recs == [("recommendations ranking 3.2 rollout",), ("recommendations model 3.1 rollout",)]
    mix = next(e for e in events if e.kind == "mix_shift")
    assert rows(path, "SELECT COUNT(*) FROM calendar WHERE start_ts = ?", (fmt(mix.start),)) == [(0,)]


def test_unknown_generator_options_are_refused_before_anything_is_written(tmp_path):
    for kw in ({"kinds": "all"}, {"release_mode": "early"}, {"release_mode": "none", "release_share": 1.5}):
        with pytest.raises(ValueError):
            sc.generate(tmp_path / "x.db", faults=2, innocents=1, seed=1, days=25, **kw)
    assert not (tmp_path / "x.db").exists()


@pytest.mark.parametrize("mode", ["none", "late", "wide"])
def test_release_mode_changes_the_release_rows_of_the_faults_only(tmp_path, mode):
    exact = sc.generate(tmp_path / "exact.db", faults=7, innocents=3, seed=5, days=35, kinds="extended")
    path = sc.generate(tmp_path / f"{mode}.db", faults=7, innocents=3, seed=5, days=35, kinds="extended",
                       release_mode=mode, release_share=1.0)
    events = sc.load_truth(path)[0]
    released = [e for e in events if e.kind in inject.RELEASE_SYSTEM]
    assert len(released) == 5 and all(e.scope.get("release") == mode for e in released)
    assert all("release" not in e.scope for e in events if e.kind not in inject.RELEASE_SYSTEM)
    def split(p):
        all_rows = rows(p, "SELECT ts, system, country, platform, description FROM releases ORDER BY rowid")
        routine = [r for r in all_rows if re.fullmatch(r"[\w-]+ rollout \(.+\)", r[4])]
        return routine, [r for r in all_rows if r not in routine]
    assert split(path)[0] == split(exact)[0]                                # the daily decoys are the same
    faults = split(path)[1]
    for e in released:
        got = [(r[0], r[2], r[3]) for r in faults if fmt(e.start) <= r[0] <= fmt(e.start + pd.Timedelta(hours=3))]
        if mode == "none":
            assert got == []
        elif mode == "late":
            assert [g[0] for g in got] == [fmt(e.start + pd.Timedelta(hours=3))]
        else:
            assert len(got) == 1 and got[0][0] == fmt(e.start)
            if e.kind == "payment_failure":
                assert got[0][1:] == (e.scope["country"], None)                 # the whole country
            else:
                assert got[0][1:] == (None, None)


def test_inject_functions_take_the_release_mode(tmp_path):
    path = backfill(str(tmp_path / "x.db"), 48, "2026-10-25T00:00:00Z", seed=2)
    con = db.connect(path)
    s = pd.Timestamp("2026-10-26T06:00:00Z")
    inject.payment_failure(con, "DE", "Android", s, release="late", replay_now=False)
    inject.payment_failure(con, "FR", "iOS", s, release="none", replay_now=False)
    inject.search_regression(con, "ES", s, release="wide", replay_now=False)
    with pytest.raises(ValueError):
        inject.payment_failure(con, "PL", "Web", s, release="sometimes", replay_now=False)
    rel = con.execute("SELECT ts, system, country, platform FROM releases ORDER BY rowid").fetchall()
    (rowid,) = con.execute("SELECT rowid FROM ground_truth WHERE scope LIKE '%\"release\": \"none\"%'").fetchone()
    inject.end_injection(con, rowid, s + pd.Timedelta(hours=4), replay_now=False)      # nothing to roll back
    after = con.execute("SELECT COUNT(*) FROM releases").fetchone()[0]
    con.close()
    assert rel == [("2026-10-26T09:00:00Z", "payments-sdk", "DE", "Android"), ("2026-10-26T06:00:00Z", "search", None, None)]
    assert after == 2


# ----------------------------------------------------------------------------- scorer rules for the new kinds
def ev(kind, **scope):
    start = pd.Timestamp("2026-11-20T08:00:00Z")
    scope = {"start": fmt(start), "end": fmt(start + pd.Timedelta(hours=6)), **scope}
    return sc.Event(kind, start, start + pd.Timedelta(hours=6), scope)


END = pd.Timestamp("2026-11-25T00:00:00Z")


def inc(segment, hours=2, hypotheses=(), report="", status="ongoing", n=1):
    return {"id": f"inc-{n}", "status": status, "first_seen": fmt(pd.Timestamp("2026-11-20T08:00:00Z") + pd.Timedelta(hours=hours)),
            "segment": segment, "hypotheses": list(hypotheses), "report": report}


def h(cause, status="supported", kind="metric"):
    return {"cause": cause, "status": status, "kind": kind}


def judge(event, incidents):
    return sc.score([event], incidents, END, 5.0)


def test_traffic_drop_cause_is_sessions_or_traffic_named_in_the_place():
    f = ev("traffic_drop", country="DE", platform="Android", severity=0.5)
    place = {"country": "DE", "platform": "Android"}
    good = [
        inc(place, hypotheses=[h("sessions fell in DE x Android with every rate unchanged")]),
        inc(place, hypotheses=[h("traffic loss in DE x Android", status="plausible"), h("release x", "rejected", "release")]),
        inc(place, report="orders fell to 120 from an expected 240 in DE x Android. Sessions fell 50% in DE x Android "
                          "while every step rate held. No cause is confirmed yet."),
    ]
    for i in good:
        r = judge(f, [i])
        assert r["detected"] == 1 and r["localized_rate"] == 1 and r["correct_cause_rate"] == 1, i
    bad = [
        inc(place, report="orders fell in DE x Android. Traffic is taken as given: only rates are judged."),
        inc(place, hypotheses=[h("release payments-sdk: payments-sdk rollout (DE, Android)", kind="release")]),
        inc(place, hypotheses=[h("a traffic loss", status="rejected")]),
        inc(place, report="Sessions fell 50% in FR x Web."),                        # another place
    ]
    for i in bad:
        r = judge(f, [i])
        assert r["detected"] == 1 and r["correct_cause_rate"] == 0, i
    assert judge(f, [inc({"country": "DE"})])["localized_rate"] == 0


@pytest.mark.parametrize("kind", ["feed_unavailable", "discount_push"])
def test_recommendation_faults_need_the_recommendations_release_supported(kind):
    f = ev(kind, channel="recommendations")
    rel = h("release recommendations: recommendations model 3.1 rollout", kind="release")
    r = judge(f, [inc({"channel": "recommendations"}, hypotheses=[rel])])
    assert (r["detected"], r["localized_rate"], r["correct_cause_rate"]) == (1, 1, 1)
    for hyps in ([h("stockout_rate rose in recommendations without a matching release")],
                 [dict(rel, status="plausible")], [h("recommendations changed", kind="metric")]):
        assert judge(f, [inc({"channel": "recommendations"}, hypotheses=hyps)])["correct_cause_rate"] == 0
    wrong_place = judge(f, [inc({"channel": "ads"}, hypotheses=[rel])])
    assert wrong_place["detected"] == 0 and wrong_place["false_alarms"] == 1
    assert judge(f, [inc({"country": "DE"}, hypotheses=[rel])])["localized_rate"] == 0


def test_a_mix_shift_is_innocent_silent_without_an_incident_or_with_an_explained_record():
    f = ev("mix_shift", category="electronics", share=0.5)
    r = judge(f, [])
    assert (r["faults"], r["innocents"], r["innocents_silent"]) == (0, 1, 1)
    explained = inc({"country": "DE"}, status="explained", hypotheses=[h("mix shift toward electronics")])
    assert judge(f, [explained])["innocents_silent"] == 1
    noisy = judge(f, [inc({"country": "DE", "category": "home"})])
    assert noisy["innocents_silent"] == 0 and noisy["false_alarms"] == 1


def test_wide_and_missing_releases_are_judged_against_the_release_that_exists():
    wide = ev("payment_failure", country="DE", platform="Android", severity=0.4, release="wide")
    hyp = h("release payments-sdk: payments-sdk 4.2 rollout (DE)", kind="release")
    assert judge(wide, [inc({"country": "DE", "platform": "Android"}, hypotheses=[hyp])])["correct_cause_rate"] == 1
    exact = ev("payment_failure", country="DE", platform="Android", severity=0.4)
    assert judge(exact, [inc({"country": "DE", "platform": "Android"}, hypotheses=[hyp])])["correct_cause_rate"] == 0
    r = judge(ev("payment_failure", country="DE", platform="Android", severity=0.4, release="none"),
              [inc({"country": "DE", "platform": "Android"}, hypotheses=[h("no cause", status="plausible")])])
    assert r["per_fault"][0]["release"] == "none" and r["correct_cause_rate"] == 0
