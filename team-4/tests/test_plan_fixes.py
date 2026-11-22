"""Tests for the changes that followed the research critique of M1 (README section 3.8):
declared outcomes, cause links from the stock level with a sign, metrics derived from any table of sums,
and a validation report that separates what holds by construction from what was checked on data."""
import numpy as np
import pandas as pd
import pytest

from brain.data import ContractError, Store
from brain.evaluator import Evaluator
from brain.graph import DEFAULT_METRICS_DIR, MetricGraph
from brain.metric_spec import SpecError, load_specs, spec_from_dict
from brain.signature import holds_by_construction, signatures
from brain.validate import summarize, validate_graph
from tests.fixture_db import make_fixture_db


def core_plus(*extra):
    return MetricGraph.from_specs(load_specs(DEFAULT_METRICS_DIR) + [spec_from_dict(e) for e in extra])


def core_changed(change):
    raws = [dict(s.raw) for s in load_specs(DEFAULT_METRICS_DIR)]
    for r in raws:
        change(r)
    return MetricGraph.from_specs([spec_from_dict(r) for r in raws])


# --- declared outcomes ---------------------------------------------------------------------------

def test_a_contributed_ratio_does_not_become_a_top_outcome():
    g = core_plus({"name": "refund_rate", "formula": "refunds / orders", "unit": "ratio"})
    assert g.top_outcomes() == ["contribution"]
    assert g.unplaced_roots() == ["refund_rate"]
    exported = g.to_dict()
    assert exported["top_outcomes"] == ["contribution"] and [t["metric"] for t in exported["trees"]] == ["contribution"]
    assert exported["unplaced_roots"] == ["refund_rate"]


def test_without_a_declared_outcome_the_structure_decides():
    g = MetricGraph.from_specs([spec_from_dict(r) for r in (
        {"name": "a", "column": "a", "decomposition": "b * c"}, {"name": "b", "column": "b"},
        {"name": "c", "formula": "a / b", "atomic": True})])
    assert g.top_outcomes() == ["a"] and g.unplaced_roots() == []


def test_outcome_flag_must_be_a_boolean():
    with pytest.raises(SpecError, match="outcome"):
        spec_from_dict({"name": "a", "column": "a", "outcome": "yes"})


# --- cause links ----------------------------------------------------------------------------------

def test_cause_links_start_at_the_stock_level_not_at_a_traffic_driven_ratio(graph):
    assert graph.out_edges("stockout_rate", "may_affect") == []
    links = {e.dst: e.attrs for e in graph.out_edges("stockouts", "may_affect")}
    assert set(links) == {"add_to_cart_rate", "feed_ctr"}
    assert links["add_to_cart_rate"]["sign"] == -1


def test_every_cause_link_says_which_way_it_pushes_or_that_it_is_unknown(graph):
    signs = {(e.src, e.dst): e.attrs["sign"] for e in graph.edges if e.kind == "may_affect"}
    assert set(signs.values()) <= {-1, 0, 1}
    assert signs[("discount_rate", "add_to_cart_rate")] == 1
    assert signs[("search_ctr", "views_per_session")] == 1


def test_sign_must_be_minus_one_zero_or_one():
    with pytest.raises(SpecError, match="sign"):
        spec_from_dict({"name": "a", "column": "a", "may_affect": [{"metric": "b", "sign": 2}]})


# --- metrics from any table of sums ---------------------------------------------------------------

def test_derive_on_actual_sums_equals_frame(graph, store):
    ev = Evaluator(graph, store)
    metrics = ["contribution", "conversion_rate", "payment_success_rate", "aov"]
    sums = ev.sums(metrics, group_by=("country", "platform"))
    pd.testing.assert_frame_equal(ev.derive(sums, metrics), ev.frame(metrics, group_by=("country", "platform")))


def test_an_expected_state_built_from_sums_keeps_every_identity(graph, store):
    """Averages of base-column sums are a coherent 'expected' world: formulas hold in it exactly."""
    ev = Evaluator(graph, store)
    t0, _ = store.time_range()
    day = pd.Timedelta(hours=24)
    metrics = ["gmv", "orders", "aov", "sessions", "conversion_rate", "contribution", "marketplace_fees",
               "ad_spend", "discounts"]
    a = ev.sums(metrics, t0, t0 + day, group_by=("country",)).set_index("country")
    b = ev.sums(metrics, t0 + day, t0 + 2 * day, group_by=("country",)).set_index("country")
    expected = ev.derive(((a + b) / 2).reset_index(), metrics)
    assert np.allclose(expected["gmv"], expected["orders"] * expected["aov"])
    assert np.allclose(expected["orders"], expected["sessions"] * expected["conversion_rate"])
    assert np.allclose(expected["contribution"],
                       expected["marketplace_fees"] + expected["ad_spend"] - expected["discounts"])


def test_derive_says_which_base_columns_are_missing(graph, store):
    ev = Evaluator(graph, store)
    with pytest.raises(KeyError, match="payment_attempts"):
        ev.derive(pd.DataFrame({"payment_successes": [1.0]}), ["payment_success_rate"])


# --- by construction vs checked on data -----------------------------------------------------------

def test_signatures_tell_same_metrics_apart_from_different_ones(graph):
    g = core_plus({"name": "cvr", "formula": "orders / sessions"},
                  {"name": "refund_rate", "formula": "refunds / orders"})
    sig = signatures(g)
    assert np.allclose(sig["cvr"], sig["conversion_rate"])
    assert not np.allclose(sig["refund_rate"], sig["conversion_rate"])


def test_core_identities_hold_by_construction_and_a_wrong_one_does_not(graph):
    assert holds_by_construction(graph, "gmv") and holds_by_construction(graph, "conversion_rate")
    def change(r):
        if r["name"] == "gmv":
            r["decomposition"] = "orders * aov * take_rate"
    assert not holds_by_construction(core_changed(change), "gmv")


def test_report_separates_definition_checks_from_data_checks(graph, store):
    checks = validate_graph(graph, store, days=2)
    basis = {(c.metric, c.check): c.basis for c in checks}
    assert basis[("gmv", "identity")] == "definition"
    assert basis[("contribution", "additivity")] == "definition"
    assert basis[("orders", "coverage")] == "data" and basis[("payment_success_rate", "bounds")] == "data"
    first = summarize(checks).splitlines()[0]
    assert "on data" in first and "by construction" in first


def test_sql_definition_is_a_data_check(store):
    g = core_plus({"name": "refund_rate", "formula": "refunds / orders", "unit": "ratio",
                   "sql": "SELECT ts, country, platform, category, channel, SUM(refunds) * 1.0 / NULLIF(SUM(orders), 0) "
                          "AS value FROM facts_hourly GROUP BY ts, country, platform, category, channel"})
    [c] = [c for c in validate_graph(g, store, days=2) if c.metric == "refund_rate" and c.check == "definition"]
    assert c.basis == "data" and c.status == "pass"


def test_coverage_fails_when_most_hours_have_no_value(tmp_path, graph):
    def mutate(df):
        df.loc[df["ts"] >= "2026-11-01T06:00:00Z", "refunds"] = None      # 18 of 24 hours without refunds
        return df
    st = Store(make_fixture_db(tmp_path / "m.db", days=1, mutate=mutate))
    [c] = [c for c in validate_graph(graph, st, days=1) if c.metric == "refunds" and c.check == "coverage"]
    assert c.status == "fail" and c.detail.startswith("6 of 24")


def test_impossible_timestamp_is_a_contract_error(tmp_path):
    def mutate(df):
        df.loc[df.index[0], "ts"] = "2026-13-45T99:00:00Z"
        return df
    with pytest.raises(ContractError):
        Store(make_fixture_db(tmp_path / "m.db", days=1, mutate=mutate))
