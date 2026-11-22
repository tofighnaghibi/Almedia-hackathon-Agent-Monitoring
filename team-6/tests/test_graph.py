"""Tests for L2 metric graph, formula/breakdown checkers, and tree view."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from graph import (
    GraphEdge,
    MetricGraph,
    build_tree,
    check_breakdown,
    check_formula,
    check_graph,
    parse_formula,
    render_text,
)

SEED_YAML = Path(__file__).resolve().parent.parent / "graph" / "seed_graph.yaml"

METRIC_DEFS: dict[str, dict[str, Any]] = {
    "orders": {"unit": "count", "is_additive": True, "dimensions": ["country", "platform", "category"]},
    "revenue": {"unit": "currency_eur", "is_additive": True, "dimensions": ["country", "platform", "category"]},
    "sessions": {"unit": "count", "is_additive": True, "dimensions": ["country", "platform", "category"]},
    "payment_attempts": {"unit": "count", "is_additive": True, "dimensions": ["country", "platform", "category"]},
    "payment_success_rate": {
        "unit": "ratio",
        "is_additive": False,
        "dimensions": ["country", "platform", "category"],
        "weight_metric": "payment_attempts",
    },
}


def _make_synthetic_df() -> pd.DataFrame:
    timestamps = pd.date_range("2026-10-01T00:00:00Z", periods=4, freq="1h", tz="UTC")
    rows = []
    for idx, ts in enumerate(timestamps):
        for country, c_mult in [("DE", 1.0), ("UK", 0.8)]:
            for platform, p_mult in [("android", 1.0), ("ios", 0.7)]:
                for category, cat_mult in [("electronics", 1.0), ("fashion", 0.9)]:
                    scale = c_mult * p_mult * cat_mult * (1.0 + 0.05 * idx)
                    sessions = int(round(1000 * scale))
                    searches = int(round(600 * scale))
                    search_clicks = int(round(210 * scale))
                    recommendation_clicks = int(round(60 * scale))
                    ad_impressions = int(round(1500 * scale))
                    ad_clicks = int(round(30 * scale))
                    ad_spend = round(ad_clicks * 0.75, 2)
                    product_clicks = search_clicks + recommendation_clicks + ad_clicks
                    cart_adds = int(round(180 * scale))
                    checkouts = int(round(90 * scale))
                    payment_attempts = int(round(80 * scale))
                    orders = int(round(75 * scale))
                    refunds = int(round(4 * scale))
                    revenue = round(orders * 60.0, 2)
                    rows.append(
                        {
                            "timestamp": ts,
                            "country": country,
                            "platform": platform,
                            "category": category,
                            "sessions": sessions,
                            "searches": searches,
                            "search_clicks": search_clicks,
                            "recommendation_clicks": recommendation_clicks,
                            "product_clicks": product_clicks,
                            "cart_adds": cart_adds,
                            "checkouts": checkouts,
                            "payment_attempts": payment_attempts,
                            "orders": orders,
                            "refunds": refunds,
                            "revenue": revenue,
                            "ad_impressions": ad_impressions,
                            "ad_clicks": ad_clicks,
                            "ad_spend": ad_spend,
                        }
                    )
    return pd.DataFrame(rows)


def _make_compute(df: pd.DataFrame):
    def compute(name: str, start: Any, end: Any, group_by: list[str] | None = None) -> pd.DataFrame:
        sub = df[(df["timestamp"] >= pd.Timestamp(start)) & (df["timestamp"] <= pd.Timestamp(end))]
        keys = ["timestamp"] + (list(group_by) if group_by else [])
        agg = sub.groupby(keys, as_index=False).sum(numeric_only=True)

        ratios = {
            "payment_success_rate": ("orders", "payment_attempts"),
            "payment_attempt_rate": ("payment_attempts", "checkouts"),
            "checkout_rate": ("checkouts", "cart_adds"),
            "add_to_cart_rate": ("cart_adds", "product_clicks"),
            "searches_per_session": ("searches", "sessions"),
            "search_ctr": ("search_clicks", "searches"),
            "average_order_value": ("revenue", "orders"),
            "aov": ("revenue", "orders"),
            "average_order_value": ("revenue", "orders"),  # name used in graph/metrics.yaml
            "refund_rate": ("refunds", "orders"),
            "ad_ctr": ("ad_clicks", "ad_impressions"),
            "cost_per_click": ("ad_spend", "ad_clicks"),
        }
        if name in ratios:
            num, den = ratios[name]
            agg["value"] = agg[num] / agg[den].replace(0, float("nan"))
        else:
            agg["value"] = agg[name].astype(float)
        return agg[keys + ["value"]]

    return compute


def _corrupt(compute, metric: str, factor: float):
    def wrapped(name: str, start: Any, end: Any, group_by: list[str] | None = None) -> pd.DataFrame:
        out = compute(name, start, end, group_by=group_by)
        if name == metric:
            out = out.copy()
            out["value"] = out["value"] * factor
        return out

    return wrapped


def _drop_slice(compute, dim: str, value: str):
    def wrapped(name: str, start: Any, end: Any, group_by: list[str] | None = None) -> pd.DataFrame:
        out = compute(name, start, end, group_by=group_by)
        if group_by and dim in group_by:
            out = out[out[dim] != value].reset_index(drop=True)
        return out

    return wrapped


def test_seed_yaml_loads_and_validates() -> None:
    mg = MetricGraph.load(SEED_YAML)
    assert len(mg.edges("formula")) == 23  # 10 two-input formulas + 1 three-input formula
    assert len(mg.edges("breakdown")) == 15  # 5 metrics x 3 dimensions
    assert len(mg.edges("possible_cause")) == 3
    assert all(e.status == "uncertain" for e in mg.edges())


def test_all_seed_formulas_and_breakdowns_pass_on_clean_data() -> None:
    df = _make_synthetic_df()
    compute = _make_compute(df)
    mg = MetricGraph.load(SEED_YAML)

    results = check_graph(
        mg,
        METRIC_DEFS,
        compute,
        start="2026-10-01T00:00:00Z",
        end="2026-10-01T03:00:00Z",
    )
    assert len(results) == 26  # 11 formulas + 15 breakdowns
    assert all(res.outcome == "pass" for res in results.values())
    assert all(e.status == "confirmed" for e in mg.edges("formula"))
    assert all(e.status == "confirmed" for e in mg.edges("breakdown"))


def test_corrupting_payment_attempts_fails_orders_formula() -> None:
    df = _make_synthetic_df()
    compute = _corrupt(_make_compute(df), "payment_attempts", 1.2)
    edge = GraphEdge(
        from_metric="payment_attempts",
        to_metric="orders",
        type="formula",
        formula="orders = payment_attempts * payment_success_rate",
    )
    res = check_formula(edge, compute, "2026-10-01T00:00:00Z", "2026-10-01T03:00:00Z")
    assert res.outcome == "fail"
    assert res.max_error is not None and res.max_error > 0.15


def test_dropping_country_slice_fails_breakdown() -> None:
    df = _make_synthetic_df()
    compute = _drop_slice(_make_compute(df), "country", "DE")
    edge = GraphEdge(from_metric="orders", to_metric="orders", type="breakdown", dimension="country")
    res = check_breakdown(
        edge, METRIC_DEFS["orders"], compute, "2026-10-01T00:00:00Z", "2026-10-01T03:00:00Z"
    )
    assert res.outcome == "fail"


def test_rate_breakdown_with_and_without_weight_metric() -> None:
    df = _make_synthetic_df()
    compute = _make_compute(df)
    edge = GraphEdge(
        from_metric="payment_success_rate",
        to_metric="payment_success_rate",
        type="breakdown",
        dimension="country",
    )
    ok_res = check_breakdown(
        edge,
        METRIC_DEFS["payment_success_rate"],
        compute,
        "2026-10-01T00:00:00Z",
        "2026-10-01T03:00:00Z",
    )
    assert ok_res.outcome == "pass"

    no_weight_def = {"unit": "ratio", "is_additive": False, "dimensions": ["country", "platform", "category"]}
    skip_res = check_breakdown(
        edge, no_weight_def, compute, "2026-10-01T00:00:00Z", "2026-10-01T03:00:00Z"
    )
    assert skip_res.outcome == "skipped"


def test_non_additive_currency_metric_is_not_summed() -> None:
    df = _make_synthetic_df()
    compute = _make_compute(df)
    edge = GraphEdge(
        from_metric="average_order_value",
        to_metric="average_order_value",
        type="breakdown",
        dimension="country",
    )
    aov_def = {"unit": "currency_eur", "is_additive": False, "dimensions": ["country"]}
    res = check_breakdown(edge, aov_def, compute, "2026-10-01T00:00:00Z", "2026-10-01T03:00:00Z")
    assert res.outcome == "skipped"


def test_parse_formula_rejects_unsafe_syntax() -> None:
    with pytest.raises(ValueError):
        parse_formula("x = __import__('os')")
    with pytest.raises(ValueError):
        parse_formula("x = len(orders)")
    with pytest.raises(ValueError):
        parse_formula("orders * rate")


def test_coexisting_breakdowns_and_formula_parents() -> None:
    mg = MetricGraph()
    mg.add_edge(GraphEdge(from_metric="orders", to_metric="orders", type="breakdown", dimension="country"))
    mg.add_edge(GraphEdge(from_metric="orders", to_metric="orders", type="breakdown", dimension="platform"))
    mg.add_edge(
        GraphEdge(
            from_metric="payment_attempts",
            to_metric="orders",
            type="formula",
            formula="orders = payment_attempts * payment_success_rate",
        )
    )
    assert len(mg.edges("breakdown")) == 2
    assert set(mg.parents("orders", "formula")) == {"payment_attempts", "payment_success_rate"}


def test_formula_edge_mismatched_to_metric_rejected() -> None:
    mg = MetricGraph()
    bad_edge = GraphEdge(
        from_metric="payment_attempts",
        to_metric="revenue",
        type="formula",
        formula="orders = payment_attempts * payment_success_rate",
    )
    with pytest.raises(ValueError):
        mg.add_edge(bad_edge)


def test_old_style_validated_edge_loads_correctly() -> None:
    edge = GraphEdge.from_dict(
        {
            "from": "orders",
            "to": "orders",
            "type": "breakdown",
            "dimension": "country",
            "validated": True,
        }
    )
    assert edge.status == "confirmed"
    assert edge.validated is True


def test_tree_build_and_render() -> None:
    mg = MetricGraph.load(SEED_YAML)
    tree = build_tree(mg, roots=("revenue", "orders"))
    text = render_text(tree)

    assert "revenue (splits: country, platform, category)" in text
    assert "orders [uncertain] (splits: country, platform, category)" in text
    # orders was already expanded under revenue, so root 'orders' is marked see above.
    assert "orders (splits: country, platform, category) [see above]" in text
