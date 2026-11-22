import sqlite3

import numpy as np
import pytest

from brain.data import Store, iso_hour
from brain.evaluator import Evaluator, NotComputable
from brain.graph import MetricGraph
from brain.metric_spec import spec_from_dict


def direct(db_path, sql):
    con = sqlite3.connect(db_path)
    try:
        return con.execute(sql).fetchone()
    finally:
        con.close()


def test_ratio_is_a_ratio_of_sums(graph, store, db_path):
    ev = Evaluator(graph, store)
    s, a = direct(db_path, "SELECT SUM(payment_successes), SUM(payment_attempts) FROM facts_hourly")
    assert ev.value("payment_success_rate") == pytest.approx(s / a, rel=1e-12)
    mean_of_ratios = direct(db_path, "SELECT AVG(CAST(payment_successes AS REAL) / payment_attempts) "
                                     "FROM facts_hourly WHERE payment_attempts > 0")[0]
    assert ev.value("payment_success_rate") != pytest.approx(mean_of_ratios, rel=1e-6)


def test_segment_filter_and_breakdown(graph, store):
    ev = Evaluator(graph, store)
    total = ev.value("sessions")
    de, fr = ev.value("sessions", segment={"country": "DE"}), ev.value("sessions", segment={"country": "FR"})
    assert de + fr == pytest.approx(total)
    by_platform = ev.breakdown("orders", "platform")
    assert set(by_platform.index) == {"iOS", "Android", "Web"}
    assert by_platform.sum() == pytest.approx(ev.value("orders"))
    both = ev.value("orders", segment={"country": "DE", "platform": "Android"})
    assert 0 < both < ev.value("orders", segment={"country": "DE"})
    multi = ev.value("orders", segment={"country": ["DE", "FR"]})
    assert multi == pytest.approx(ev.value("orders"))


def test_series_window_and_formula(graph, store):
    ev = Evaluator(graph, store)
    t0, t1 = store.time_range()
    s = ev.series("contribution", t0, t0 + np.timedelta64(24, "h"))
    assert len(s) == 24 and s.index.is_monotonic_increasing
    f = ev.frame(["contribution", "marketplace_fees", "ad_spend", "discounts"], t0, t1, by_hour=True)
    assert np.allclose(f["contribution"], f["marketplace_fees"] + f["ad_spend"] - f["discounts"])


def test_decomposition_expression_reproduces_metric(graph, store):
    ev = Evaluator(graph, store)
    inv = graph.specs["conversion_rate"].investigative_formula
    e = ev.expression(inv, group_by=("country",))
    m = ev.frame(["conversion_rate"], group_by=("country",))
    assert np.allclose(e["value"].to_numpy(), m["conversion_rate"].to_numpy())


def test_window_is_half_open(graph, store):
    ev = Evaluator(graph, store)
    t0, _ = store.time_range()
    one = ev.frame(["sessions"], t0, t0 + np.timedelta64(1, "h"), by_hour=True)
    assert len(one) == 1 and iso_hour(one["ts"].iloc[0]) == iso_hour(t0)


def test_sql_only_metric_is_not_computable_yet(store):
    g = MetricGraph.from_specs([spec_from_dict({"name": "x", "sql": "SELECT 1"})])
    with pytest.raises(NotComputable):
        Evaluator(g, store).value("x")


def test_store_guards(db_path, tmp_path):
    with pytest.raises(FileNotFoundError):
        Store(tmp_path / "missing.db")
    st = Store(db_path)
    with pytest.raises(KeyError):
        st.sums(["orders; DROP TABLE facts_hourly"])
    with pytest.raises(KeyError):
        st.sums(["orders"], group_by=["planet"])
    with pytest.raises(KeyError):
        st.sums(["orders"], segment={"planet": "Mars"})
    counts = st.row_counts()
    assert set(counts) == {"facts_hourly", "ingestion_log", "releases", "calendar"}


def test_brain_cannot_read_the_answer(db_path):
    """Guardrail 2: brain/ reads only the contract tables, never the scoring table."""
    st = Store(db_path)
    assert "ground_truth" not in st.tables()
    with pytest.raises(PermissionError):
        st.read_sql("SELECT * FROM ground_truth")
    with pytest.raises(PermissionError):
        st.read_sql("SELECT f.ts FROM facts_hourly f JOIN ground_truth g ON 1=1")
    assert len(st.read_sql('SELECT COUNT(*) AS n FROM "releases"')) == 1
