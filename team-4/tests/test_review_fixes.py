"""Regression tests for the findings of the M1 review (one test per confirmed finding)."""
import shutil
import sqlite3

import numpy as np
import pandas as pd
import pytest
import yaml

from brain.data import ContractError, Store
from brain.evaluator import Evaluator
from brain.graph import GraphError, MetricGraph, main
from brain.metric_spec import SpecError, load_specs, spec_from_dict
from brain.validate import validate_graph
from tests.fixture_db import make_fixture_db

REFUND_SQL = ("SELECT ts, country, platform, category, channel, "
              "SUM(refunds) * 1.0 / NULLIF(SUM(orders), 0) AS value FROM facts_hourly "
              "GROUP BY ts, country, platform, category, channel")


def core_plus(*extra):
    return MetricGraph.from_specs(load_specs(MetricGraph.load.__defaults__[0]) + [spec_from_dict(e) for e in extra])


def by(checks, metric, check):
    return [c for c in checks if c.metric == metric and c.check == check]


# --- a contributed metric's SQL is run and compared with its formula ---------------------------

def test_sql_with_correct_formula_passes(store):
    g = core_plus({"name": "refund_rate", "sql": REFUND_SQL, "formula": "refunds / orders", "unit": "ratio"})
    checks = validate_graph(g, store, days=2)
    [c] = by(checks, "refund_rate", "definition")
    assert c.status == "pass", c.detail
    assert g.status["refund_rate"] == "validated"


def test_sql_with_wrong_formula_fails(store):
    g = core_plus({"name": "refund_rate", "sql": REFUND_SQL, "formula": "refunds / sessions", "unit": "ratio"})
    checks = validate_graph(g, store, days=2)
    [c] = by(checks, "refund_rate", "definition")
    assert c.status == "fail"
    assert g.status["refund_rate"] == "failed"
    assert by(checks, "refund_rate", "identity")[0].status != "skip"


def test_sql_dimensions_must_match_declared(store):
    g = core_plus({"name": "refund_rate", "sql": REFUND_SQL, "formula": "refunds / orders",
                   "dimensions": ["country"]})
    [c] = by(validate_graph(g, store, days=2), "refund_rate", "definition")
    assert c.status == "fail" and "declares dimensions" in c.detail


def test_sql_only_metric_waits_for_a_formula(store):
    g = core_plus({"name": "refund_rate", "sql": REFUND_SQL})
    checks = validate_graph(g, store, days=2)
    [c] = by(checks, "refund_rate", "definition")
    assert c.status == "skip" and "infer" in c.detail
    assert g.status["refund_rate"] == "unvalidated"


def test_sql_reading_the_answer_is_a_failed_definition(store):
    g = core_plus({"name": "cheat", "formula": "orders / sessions",
                   "sql": "SELECT injected_at AS ts, kind AS country, scope AS platform, description AS category, "
                          "kind AS channel, 1.0 AS value FROM ground_truth"})
    [c] = by(validate_graph(g, store, days=2), "cheat", "definition")
    assert c.status == "fail" and "refused" in c.detail


# --- guardrail 2 inside SQLite --------------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "SELECT * FROM facts_hourly, ground_truth LIMIT 1",
    "SELECT * FROM main.ground_truth",
    'SELECT * FROM "main"."ground_truth"',
    "SELECT ts FROM facts_hourly UNION SELECT injected_at FROM ground_truth",
    "WITH x AS (SELECT * FROM ground_truth) SELECT * FROM x",
    "SELECT (SELECT COUNT(*) FROM ground_truth) AS n",
    "SELECT * FROM /* hi */ ground_truth",
    "SELECT name FROM sqlite_master",
])
def test_whitelist_cannot_be_bypassed(store, sql):
    with pytest.raises(PermissionError):
        store.read_sql(sql)


def test_legitimate_joins_of_contract_tables_still_work(store):
    df = store.read_sql("SELECT COUNT(*) AS n FROM facts_hourly f, ingestion_log i WHERE f.ts = i.ts AND i.source = 'orders'")
    assert df["n"].iloc[0] > 0


def test_attach_and_pragma_are_refused(store, tmp_path):
    with pytest.raises(Exception):
        store.read_sql(f"ATTACH DATABASE '{tmp_path / 'x.db'}' AS x")
    with pytest.raises(Exception):
        store.read_sql("PRAGMA table_info(ground_truth)")


def test_path_with_hash_opens_read_only(db_path, tmp_path):
    d = tmp_path / "a#b"
    d.mkdir()
    copy = d / "market.db"
    shutil.copy(db_path, copy)
    st = Store(copy)
    assert st.row_counts()["facts_hourly"] > 0
    with pytest.raises(Exception):
        st.conn.execute("CREATE TABLE x (a)")


# --- timestamps, NULLs, missing hours, missing columns -------------------------------------------

def test_other_timestamp_format_is_a_loud_error(tmp_path):
    def mutate(df):
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.strftime("%Y-%m-%d %H:%M:%S+00:00")
        return df
    with pytest.raises(ContractError, match="2026-11-01T13:00:00Z"):
        Store(make_fixture_db(tmp_path / "m.db", days=1, mutate=mutate))


def test_null_cell_makes_the_hour_nan_not_a_drop(tmp_path, graph):
    def mutate(df):
        df.loc[df.index[5], "payment_successes"] = None
        return df
    st = Store(make_fixture_db(tmp_path / "m.db", days=1, mutate=mutate))
    ev = Evaluator(graph, st)
    t0, _ = st.time_range()
    s = ev.series("payment_success_rate", t0, t0 + pd.Timedelta(hours=24))
    assert np.isnan(s.iloc[0]) and np.isfinite(s.iloc[1:]).all()


def test_missing_hour_is_nan_not_a_gap(tmp_path, graph):
    def mutate(df):
        return df[df["ts"] != "2026-11-01T05:00:00Z"]
    st = Store(make_fixture_db(tmp_path / "m.db", days=1, mutate=mutate))
    s = Evaluator(graph, st).series("orders", "2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z")
    assert len(s) == 24 and np.isnan(s.loc["2026-11-01T05:00:00Z"]) and s.drop(pd.Timestamp("2026-11-01T05:00:00Z", tz="UTC")).notna().all()


def test_missing_column_fails_and_does_not_crash(tmp_path, graph):
    st = Store(make_fixture_db(tmp_path / "m.db", days=2, mutate=lambda df: df.drop(columns=["payment_attempts"])))
    checks = validate_graph(graph, st, days=2)
    fails = {(c.metric, c.check) for c in checks if c.status == "fail"}
    assert ("payment_attempts", "coverage") in fails
    assert ("payment_success_rate", "coverage") in fails
    assert ("conversion_rate", "identity") in fails or ("conversion_rate", "coverage") in fails
    assert graph.status["payment_attempts"] == "failed"


# --- graph structure ------------------------------------------------------------------------------

def test_definition_cycle_is_rejected():
    with pytest.raises(GraphError, match="definition cycle"):
        MetricGraph.from_specs([spec_from_dict(r) for r in (
            {"name": "a", "formula": "b / c"}, {"name": "b", "formula": "a * c"}, {"name": "c", "column": "c"})])


def test_flags_must_be_booleans():
    with pytest.raises(SpecError, match="atomic"):
        spec_from_dict({"name": "a", "formula": "b / c", "atomic": "false"})


def test_freshness_is_inherited_by_derived_metrics(graph):
    ctx = {n.id for n in graph.context_of("conversion_rate")}
    assert {"freshness:orders", "freshness:traffic"} <= ctx
    assert {"ads", "orders"} <= set(graph.sources("contribution"))


def test_ads_split_traffic_from_pacing(graph):
    assert graph.formula_children("ad_impressions") == [("sessions", "factor"), ("ad_load", "factor")]
    assert not any(e.dst == "sessions" for e in graph.out_edges("ad_clicks", "may_affect"))


def test_feed_to_availability_path(graph):
    assert "release:recommendations" in {n.id for n in graph.context_of("stockouts")}
    # cause links moved from stockout_rate to stockouts: the ratio follows traffic (see tests/test_plan_fixes.py)
    assert {e.dst for e in graph.out_edges("stockouts", "may_affect")} == {"add_to_cart_rate", "feed_ctr"}


# --- windows, segments, status propagation, CLI ---------------------------------------------------

def test_window_start_mid_data(graph, store, db_path):
    t0, _ = store.time_range()
    a, b = t0 + pd.Timedelta(hours=24), t0 + pd.Timedelta(hours=26)
    con = sqlite3.connect(db_path)
    direct = con.execute("SELECT SUM(sessions) FROM facts_hourly WHERE ts >= ? AND ts < ?",
                         (a.strftime("%Y-%m-%dT%H:00:00Z"), b.strftime("%Y-%m-%dT%H:00:00Z"))).fetchone()[0]
    con.close()
    ev = Evaluator(graph, store)
    assert ev.value("sessions", a, b) == direct
    assert len(ev.frame(["sessions"], a, b, by_hour=True)) == 2
    cov = by(validate_graph(graph, store, days=2), "orders", "coverage")[0]
    assert cov.detail.startswith("48 of 48")


def test_series_and_breakdown_respect_segment(graph, store):
    ev = Evaluator(graph, store)
    de = ev.value("orders", segment={"country": "DE"})
    assert ev.series("orders", segment={"country": "DE"}).sum() == pytest.approx(de)
    assert de < ev.value("orders")
    assert ev.breakdown("orders", "platform", segment={"country": "DE"}).sum() == pytest.approx(de)


def test_failure_status_propagates_to_edges_and_export(store):
    raws = [s.raw for s in load_specs(MetricGraph.load.__defaults__[0])]
    for r in raws:
        if r["name"] == "gmv":
            r["decomposition"] = "orders * aov * take_rate"
        if r["name"] == "payment_success_rate":
            r["bounds"] = [0, 0.5]
    g = MetricGraph.from_specs([spec_from_dict(r) for r in raws])
    validate_graph(g, store, days=2)
    assert all(g.status[e.id] == "failed" for e in g.out_edges("gmv", "formula"))
    assert g.status["payment_success_rate"] == "failed"
    exported = {n["id"]: n["status"] for n in g.to_dict()["nodes"]}
    assert exported["gmv"] == "failed" and exported["payment_success_rate"] == "failed"


def test_cli_exit_code_reflects_failures(tmp_path, db_path):
    raws = [s.raw for s in load_specs(MetricGraph.load.__defaults__[0])]
    for r in raws:
        if r["name"] == "take_rate":
            r["bounds"] = [0, 0.01]
    (tmp_path / "m.yaml").write_text(yaml.safe_dump({"metrics": raws}), encoding="utf-8")
    assert main(["--metrics", str(tmp_path / "m.yaml"), "--validate", "--db", str(db_path)]) == 1
    assert main(["--validate", "--db", str(db_path)]) == 0


def test_unknown_release_system_is_a_warning(tmp_path, graph):
    st = Store(make_fixture_db(tmp_path / "m.db", days=1,
                               releases=[("2026-11-01T03:00:00Z", "teleport", "DE", "Android", "v1")]))
    warns = [c for c in validate_graph(graph, st, days=1) if c.status == "warn"]
    assert any("teleport" in c.detail for c in warns)


def test_simulator_release_system_is_linked_to_payment_metrics(tmp_path, graph):
    """The simulator writes releases.system = 'payments-sdk' (sim/inject.py)."""
    for m in ("payment_success_rate", "payment_attempt_rate", "payment_successes", "payment_attempts"):
        assert "release:payments-sdk" in {n.id for n in graph.context_of(m)}, m
    st = Store(make_fixture_db(tmp_path / "m.db", days=1,
                               releases=[("2026-11-01T03:00:00Z", "payments-sdk", "DE", "Android", "4.2")]))
    assert [c.detail for c in validate_graph(graph, st, days=1) if c.status == "warn"] == []
