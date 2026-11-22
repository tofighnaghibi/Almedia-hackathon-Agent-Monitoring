"""L5 joint test: the agent loop (B) with the real investigation tools (A) on simulator data.

Work plan check: for each injected fault, the top explanation matches the
ground truth. Uses the deterministic FallbackChooser (no LLM).
"""

import warnings

import pandas as pd
import pytest

from agent.chooser import FallbackChooser
from agent.loop import RealInvestigationTools, investigate
from graph import MetricEngine, MetricGraph, run_checks
from graph.__main__ import DEFAULT_GRAPH
from monitoring.data_quality import check_dq
from monitoring.detect import Detector
from monitoring.event_calendar import EventCalendar
from simulator import SimConfig
from simulator.pipeline import build_dataset

CFG = SimConfig(start="2026-10-01", days=28, seed=42)


@pytest.fixture(scope="module")
def setup():
    result, truth = build_dataset(CFG, "all")
    table = result.marketplace_hourly
    engine = MetricEngine(tables={"marketplace_hourly": table})
    graph = MetricGraph.load(DEFAULT_GRAPH)
    assert run_checks(engine, graph).ok
    for name in engine.registry.names():
        graph.graph.add_node(name)
    cal = EventCalendar.from_dataframe(result.events)
    slices = {d: sorted(table[d].unique()) for d in ["country", "platform", "category"]}

    def dq(start, end, segment):
        return check_dq(table, start, end, now=end, expected_slices=slices, segment=segment)

    det = Detector(engine)
    tools = RealInvestigationTools(engine, graph, detector=det)
    return engine, graph, cal, dq, det, tools, {s["scenario_id"]: s for s in truth["scenarios"]}


def _run(setup, metric, by, segment, start, end):
    engine, graph, cal, dq, det, tools, _ = setup
    eps = [e for e in det.detect([metric], by=by, start=start, end=end) if e.segment == segment]
    assert eps, f"no {metric} anomaly in {segment}"
    ep = max(eps, key=lambda e: abs(e.peak_z))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return investigate(ep, graph, tools, cal, dq, chooser=FallbackChooser())


def _causes(finding):
    """Ranked hypotheses that are not symptoms and not rejected."""
    return [h for h in finding["hypotheses"] if not h["is_symptom"] and h["status"] != "rejected"]


def test_payment_failure(setup):
    f = _run(setup, "orders", ["country"], {"country": "DE"}, "2026-10-22T10:00Z", "2026-10-23T10:00Z")
    top = _causes(f)[0]
    assert (top["node"], top["segment"], top["status"]) == (
        "payment_success_rate", {"country": "DE", "platform": "android"}, "confirmed")
    assert f["stop_reason"] == "explained_and_confirmed" and f["explained_share"] >= 0.9
    # the release right before it is a candidate cause, not a confirmed one
    rel = next(h for h in f["hypotheses"] if h["source"] == "event" and "evt_001" in h["cause"])
    assert rel["segment"] == {"country": "DE", "platform": "android"} and rel["status"] == "uncertain"
    # payment attempts did not change: no false confirmation
    attempts = next(h for h in f["hypotheses"] if h["node"] == "payment_attempts")
    assert attempts["status"] != "confirmed" and attempts["explained_share"] < 0.1


def test_search_release(setup):
    f = _run(setup, "orders", ["country"], {"country": "DE"}, "2026-10-23T10:00Z", "2026-10-24T04:00Z")
    top = _causes(f)[0]
    assert (top["node"], top["segment"], top["status"]) == ("search_ctr", {"country": "DE"}, "confirmed")
    confirmed = {h["node"] for h in f["hypotheses"] if h["status"] == "confirmed"}
    assert "payment_success_rate" not in confirmed and "payment_attempts" not in confirmed


def test_two_faults_in_one_window_are_both_found(setup):
    """Back-to-back faults merge into one orders episode; both causes come out confirmed."""
    f = _run(setup, "orders", ["country"], {"country": "DE"}, "2026-10-22", None)
    confirmed = {(h["node"], tuple(sorted(h["segment"].items()))) for h in f["hypotheses"] if h["status"] == "confirmed"}
    assert ("search_ctr", (("country", "DE"),)) in confirmed
    assert ("payment_success_rate", (("country", "DE"), ("platform", "android"))) in confirmed


def test_ad_budget_fault_is_localized_but_not_overclaimed(setup):
    f = _run(setup, "ad_spend", ["country"], {"country": "FR"}, "2026-10-22", None)
    top = _causes(f)[0]
    assert (top["node"], top["segment"]) == ("ad_impressions", {"country": "FR"})
    # no release explains the timing, so it stays a hypothesis
    assert top["status"] == "uncertain" and top["missing_conditions"] == ["timing_aligned"]


def test_stock_out_points_upstream_to_out_of_stock_rate(setup):
    # monitoring starts at the beginning of the week, before the stock-out
    f = _run(setup, "orders", ["category"], {"category": "electronics"}, "2026-10-22", None)
    top = _causes(f)[0]
    assert (top["node"], top["segment"], top["source"]) == (
        "out_of_stock_rate", {"category": "electronics"}, "graph_cause_edge")
    # a possible-cause edge supports a hypothesis, not a confirmed cause
    assert top["status"] == "uncertain" and top["missing_conditions"] == ["mechanism_confirmed"]
    # the funnel metrics below cart adds are its symptoms
    atc = next(h for h in f["hypotheses"] if h["node"] == "add_to_cart_rate")
    assert atc["is_symptom"]


def test_late_data_stops_at_the_data_quality_gate(setup):
    f = _run(setup, "row_count", ["country", "platform"], {"country": "DE", "platform": "ios"}, "2026-10-26", None)
    assert f["stop_reason"] == "insufficient_evidence"
    assert all(h["status"] != "confirmed" for h in f["hypotheses"])


def test_counterfactual_works_for_ratios(setup):
    engine, graph, cal, dq, det, tools, _ = setup
    s, e = pd.Timestamp("2026-10-22T10:00Z"), pd.Timestamp("2026-10-23T10:00Z")
    good = tools.counterfactual("payment_success_rate", s, e, {"country": "DE", "platform": "android"},
                                parent_segment={"country": "DE"})
    bad = tools.counterfactual("payment_success_rate", s, e, {"country": "DE", "platform": "ios"},
                               parent_segment={"country": "DE"})
    assert good["passed"] and good["recovery_share"] > 0.9
    assert not bad["passed"] and bad["recovery_share"] < 0.3
