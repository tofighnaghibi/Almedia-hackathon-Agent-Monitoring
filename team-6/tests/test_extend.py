"""Tests for L3 graph extension: validation, ambiguity resolution, and registration."""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd

from graph import GraphEdge, MetricGraph
from graph.extend import Candidate, extend_graph, resolve_ambiguity, validate_candidate


class _DummyMonitorRegistry:
    def __init__(self) -> None:
        self.registered: list[str] = []

    def register(self, metric_name: str) -> None:
        self.registered.append(metric_name)


def _make_base_graph() -> MetricGraph:
    mg = MetricGraph()
    mg.add_edge(
        GraphEdge(
            from_metric="payment_attempts",
            to_metric="orders",
            type="formula",
            formula="orders = payment_attempts * payment_success_rate",
            status="confirmed",
        )
    )
    mg.add_edge(
        GraphEdge(from_metric="sessions", to_metric="sessions", type="breakdown", dimension="country")
    )
    mg.add_edge(
        GraphEdge(
            from_metric="out_of_stock_rate",
            to_metric="cart_adds",
            type="possible_cause",
            confidence=0.5,
        )
    )
    return mg


def _make_synthetic_df(hours: int = 24) -> pd.DataFrame:
    timestamps = pd.date_range("2026-10-01T00:00:00Z", periods=hours, freq="1h", tz="UTC")
    rows = []
    for i, ts in enumerate(timestamps):
        trend = 1.0 + 0.4 * np.sin(i / 3.0)
        for country, c_mult in [("DE", 1.0), ("UK", 0.8)]:
            for platform, p_mult in [("android", 1.0), ("ios", 0.7)]:
                sessions = int(round(1000 * c_mult * p_mult * trend))
                orders = int(round(80 * c_mult * p_mult * trend))
                # Refund rate matches orders * 0.05 everywhere except DE/android
                refunds = int(round(orders * (0.20 if (country == "DE" and platform == "android") else 0.05)))
                rows.append(
                    {
                        "timestamp": ts,
                        "country": country,
                        "platform": platform,
                        "sessions": sessions,
                        "orders": orders,
                        "refunds": refunds,
                        "payment_attempts": int(round(orders / 0.95)),
                        # Independent lagged driver: ad_spend peaks 1h before signups_lagged
                        "ad_spend": 100.0 + 50.0 * np.cos(i / 2.0),
                        "cart_adds": int(round(180 * c_mult * p_mult * trend)),
                        # Two series driven purely by sessions trend
                        "searches": int(round(sessions * 0.6)),
                        "checkouts": int(round(sessions * 0.12)),
                    }
                )
    df = pd.DataFrame(rows)
    # Create a true lagged metric at lag=1h from ad_spend (after aggregating by timestamp)
    hourly_ad = df.groupby("timestamp")["ad_spend"].sum().shift(1).bfill()
    df["lagged_clicks"] = df["timestamp"].map(hourly_ad) * 0.25
    return df


def _make_compute(df: pd.DataFrame):
    def compute(name: str, start: Any, end: Any, group_by: list[str] | None = None) -> pd.DataFrame:
        sub = df[(df["timestamp"] >= pd.Timestamp(start)) & (df["timestamp"] <= pd.Timestamp(end))]
        keys = ["timestamp"] + (list(group_by) if group_by else [])
        agg = sub.groupby(keys, as_index=False).sum(numeric_only=True)
        if name == "refund_rate":
            agg["value"] = agg["refunds"] / agg["orders"].replace(0, float("nan"))
        elif name == "payment_success_rate":
            agg["value"] = agg["orders"] / agg["payment_attempts"].replace(0, float("nan"))
        elif name == "order_rate":
            agg["value"] = agg["orders"] / agg["sessions"].replace(0, float("nan"))
        elif name == "approx_order_rate":
            # Within ~0.3% of order_rate (different denominator scaled by 1.003)
            agg["value"] = agg["orders"] / (agg["sessions"] * 1.003)
        elif name in agg.columns:
            agg["value"] = agg[name].astype(float)
        else:
            raise KeyError(name)
        return agg[keys + ["value"]]

    return compute


def test_clean_ratio_formula_confirmed() -> None:
    df = _make_synthetic_df()
    compute = _make_compute(df)
    mg = _make_base_graph()

    cand = Candidate(
        new_metric="order_rate",
        type="formula",
        from_metric="orders",
        to_metric="order_rate",
        formula="order_rate = orders / sessions",
        source="auto_discovered",
    )
    res = validate_candidate(cand, mg, compute, "2026-10-01T00:00:00Z", "2026-10-01T23:00:00Z")
    assert res.status == "confirmed"
    assert res.confidence >= 0.99
    assert "max error" in res.evidence


def test_plausible_but_wrong_formula_rejected() -> None:
    df = _make_synthetic_df()
    compute = _make_compute(df)
    mg = _make_base_graph()

    wrong = Candidate(
        new_metric="order_rate",
        type="formula",
        from_metric="orders",
        to_metric="order_rate",
        formula="order_rate = orders / payment_attempts",
        source="llm_suggested",
    )
    res = validate_candidate(wrong, mg, compute, "2026-10-01T00:00:00Z", "2026-10-01T23:00:00Z")
    assert res.status == "rejected"
    assert res.max_error is not None and res.max_error > 0.01
    assert "max error" in res.evidence


def test_ambiguous_formulas_downgraded_with_suggested_checks() -> None:
    df = _make_synthetic_df()
    compute = _make_compute(df)
    mg = _make_base_graph()

    c1 = Candidate(
        new_metric="order_rate",
        type="formula",
        from_metric="orders",
        to_metric="order_rate",
        formula="order_rate = orders / sessions",
        source="auto_discovered",
    )
    c2 = Candidate(
        new_metric="order_rate",
        type="formula",
        from_metric="orders",
        to_metric="order_rate",
        formula="order_rate = orders / (sessions * 1.003)",
        source="llm_suggested",
    )
    r1 = validate_candidate(c1, mg, compute, "2026-10-01T00:00:00Z", "2026-10-01T23:00:00Z")
    r2 = validate_candidate(c2, mg, compute, "2026-10-01T00:00:00Z", "2026-10-01T23:00:00Z")
    resolved = resolve_ambiguity([r1, r2])

    assert all(r.status == "uncertain" for r in resolved)
    assert all(r.confidence <= 0.6 for r in resolved)
    assert all("ambiguous: also fits" in r.evidence for r in resolved)
    assert all(len(r.suggested_checks) >= 1 for r in resolved)


def test_ranked_ambiguity_keeps_top_candidate_confirmed() -> None:
    df = _make_synthetic_df()
    compute = _make_compute(df)
    mg = _make_base_graph()

    c_top = Candidate(
        new_metric="order_rate",
        type="formula",
        from_metric="orders",
        to_metric="order_rate",
        formula="order_rate = orders / sessions",
        source="auto_discovered",
        rationale="rank=1; exact metric definition",
    )
    c_alt = Candidate(
        new_metric="order_rate",
        type="formula",
        from_metric="orders",
        to_metric="order_rate",
        formula="order_rate = orders / (sessions * 1.003)",
        source="auto_discovered",
        rationale="rank=2; secondary fit",
    )
    r_top = validate_candidate(c_top, mg, compute, "2026-10-01T00:00:00Z", "2026-10-01T23:00:00Z")
    r_alt = validate_candidate(c_alt, mg, compute, "2026-10-01T00:00:00Z", "2026-10-01T23:00:00Z")
    resolved = resolve_ambiguity([r_top, r_alt])

    assert resolved[0].status == "confirmed"
    assert resolved[1].status == "uncertain"
    assert resolved[1].confidence <= 0.6


def test_per_slice_failure_noted_on_confirmed_formula() -> None:
    df = _make_synthetic_df()
    compute = _make_compute(df)
    mg = _make_base_graph()

    # Create a metric that equals orders * 0.05 overall? Let's test a formula that holds
    # within 1% on UK and iOS but fails on DE/android:
    # We'll inject a 5% discrepancy only in DE & android on a custom compute wrapper.
    def slice_broken_compute(name: str, start: Any, end: Any, group_by: list[str] | None = None) -> pd.DataFrame:
        out = compute(name, start, end, group_by=group_by)
        if name == "order_rate" and group_by:
            out = out.copy()
            if "country" in group_by:
                out.loc[out["country"] == "DE", "value"] *= 1.15
            if "platform" in group_by:
                out.loc[out["platform"] == "android", "value"] *= 1.15
        return out

    cand = Candidate(
        new_metric="order_rate",
        type="formula",
        from_metric="orders",
        to_metric="order_rate",
        formula="order_rate = orders / sessions",
        source="auto_discovered",
    )
    res = validate_candidate(
        cand, mg, slice_broken_compute, "2026-10-01T00:00:00Z", "2026-10-01T23:00:00Z"
    )
    assert res.status == "confirmed"
    assert "holds except" in res.evidence
    assert "country=DE" in res.failing_slices
    assert "platform=android" in res.failing_slices


def test_possible_cause_true_driver_and_traffic_confounded() -> None:
    df = _make_synthetic_df(hours=36)
    compute = _make_compute(df)
    mg = _make_base_graph()
    mg.graph.add_node("ad_spend")
    mg.graph.add_node("searches")
    mg.graph.add_node("checkouts")

    # True lagged driver (ad_spend -> lagged_clicks at lag 1h)
    true_cause = Candidate(
        new_metric="lagged_clicks",
        type="possible_cause",
        from_metric="ad_spend",
        to_metric="lagged_clicks",
        lag_hours=1,
        source="auto_discovered",
    )
    res_true = validate_candidate(
        true_cause, mg, compute, "2026-10-01T00:00:00Z", "2026-10-02T11:00:00Z"
    )
    assert res_true.status == "uncertain"
    assert res_true.confidence >= 0.6

    # Purely traffic-driven pair (searches -> checkouts both follow sessions)
    traffic_cause = Candidate(
        new_metric="checkouts",
        type="possible_cause",
        from_metric="searches",
        to_metric="checkouts",
        lag_hours=0,
        source="llm_suggested",
    )
    res_traffic = validate_candidate(
        traffic_cause, mg, compute, "2026-10-01T00:00:00Z", "2026-10-02T11:00:00Z"
    )
    assert res_traffic.status == "uncertain"
    assert "explained by traffic" in res_traffic.evidence


def test_unknown_metric_rejected() -> None:
    df = _make_synthetic_df()
    compute = _make_compute(df)
    mg = _make_base_graph()

    bad = Candidate(
        new_metric="order_rate",
        type="formula",
        from_metric="nonexistent_metric",
        to_metric="order_rate",
        formula="order_rate = nonexistent_metric / sessions",
        source="llm_suggested",
    )
    res = validate_candidate(bad, mg, compute, "2026-10-01T00:00:00Z", "2026-10-01T23:00:00Z")
    assert res.status == "rejected"
    assert "unknown metric" in res.evidence


def test_extend_graph_registers_metric_and_filters_rejected() -> None:
    df = _make_synthetic_df()
    compute = _make_compute(df)
    mg = _make_base_graph()
    monitor = _DummyMonitorRegistry()

    metric_def = {
        "name": "order_rate",
        "description": "Orders per session",
        "unit": "ratio",
        "calculation": "SUM(orders) / NULLIF(SUM(sessions), 0)",
        "dimensions": ["country", "platform"],
        "is_additive": False,
        "weight_metric": "sessions",
    }
    candidates = [
        Candidate(
            new_metric="order_rate",
            type="formula",
            from_metric="orders",
            to_metric="order_rate",
            formula="order_rate = orders / sessions",
            source="auto_discovered",
        ),
        Candidate(
            new_metric="order_rate",
            type="formula",
            from_metric="orders",
            to_metric="order_rate",
            formula="order_rate = orders / payment_attempts",
            source="llm_suggested",
        ),
        Candidate(
            new_metric="order_rate",
            type="breakdown",
            from_metric="order_rate",
            to_metric="order_rate",
            dimension="country",
            source="auto_discovered",
        ),
        Candidate(
            new_metric="order_rate",
            type="breakdown",
            from_metric="order_rate",
            to_metric="order_rate",
            dimension="category",  # not allowed in metric_def
            source="llm_suggested",
        ),
    ]

    report = extend_graph(
        mg,
        metric_def,
        candidates,
        compute,
        "2026-10-01T00:00:00Z",
        "2026-10-01T23:00:00Z",
        monitor_registry=monitor,
    )

    assert report.metric == "order_rate"
    assert monitor.registered == ["order_rate"]
    assert len(report.added_edges) == 2
    assert len(report.rejected_edges) == 2
    assert set(mg.parents("order_rate", "formula")) == {"orders", "sessions"}
    # Ensure report is JSON-serializable
    parsed = json.loads(report.to_json())
    assert parsed["metric"] == "order_rate"
