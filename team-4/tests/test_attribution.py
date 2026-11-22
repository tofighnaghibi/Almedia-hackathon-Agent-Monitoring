"""Attribution of an outcome's change to the atomic factors of the formula graph (README 3.5)."""
import math

import pytest

from brain.attribution import attribute, expand, leaves
from brain.graph import MetricGraph
from brain.impact import recompute
from brain.metric_spec import spec_from_dict

BASE = dict(sessions=10000.0, views_per_session=1.5, add_to_cart_rate=0.08, checkout_rate=0.5,
            payment_attempt_rate=1.05, payment_success_rate=0.94, order_rate=0.995, aov=64.0,
            take_rate=0.12, discount_rate=0.045, ad_load=3.0, ad_ctr=0.02, ad_cpc=0.35)


@pytest.fixture(scope="module")
def graph():
    return MetricGraph.load()


def changed(**kw):
    return dict(BASE, **kw)


def test_the_outcome_is_a_sum_of_signed_products_of_atomic_factors(graph):
    assert set(leaves(graph, "contribution")) == set(BASE)
    terms = expand(graph, "contribution")
    funnel = ("sessions", "views_per_session", "add_to_cart_rate", "checkout_rate", "payment_attempt_rate",
              "payment_success_rate", "order_rate", "aov")
    assert sorted((s, tuple(sorted(f))) for s, f in terms) == sorted([
        (1, tuple(sorted(funnel + ("take_rate",)))),
        (-1, tuple(sorted(funnel + ("discount_rate",)))),
        (1, tuple(sorted(("sessions", "ad_load", "ad_ctr", "ad_cpc")))),
    ])


def test_one_changed_factor_gets_the_whole_change_along_every_path(graph):
    actual = changed(payment_success_rate=0.60)
    a = attribute(graph, "contribution", actual, BASE)
    assert a.method == "lmdi"
    assert a.by_factor["payment_success_rate"] == pytest.approx(a.actual - a.expected, rel=1e-12)
    assert all(v == 0 for k, v in a.by_factor.items() if k != "payment_success_rate")
    # fewer successful payments mean fewer fees AND fewer discounts given away; ad money is untouched
    fees = recompute(graph, "marketplace_fees", actual, {}) - recompute(graph, "marketplace_fees", BASE, {})
    disc = recompute(graph, "discounts", actual, {}) - recompute(graph, "discounts", BASE, {})
    assert a.by_factor["payment_success_rate"] == pytest.approx(fees - disc, rel=1e-12)
    assert fees < 0 and disc < 0


def test_several_factors_split_the_change_exactly_with_the_right_signs(graph):
    actual = changed(payment_success_rate=0.70, add_to_cart_rate=0.07, ad_load=4.5, discount_rate=0.06, sessions=11000.0)
    a = attribute(graph, "contribution", actual, BASE)
    assert sum(a.by_factor.values()) == pytest.approx(a.actual - a.expected, rel=1e-12)
    assert a.by_factor["payment_success_rate"] < 0 and a.by_factor["add_to_cart_rate"] < 0
    assert a.by_factor["ad_load"] > 0 and a.by_factor["sessions"] > 0
    assert a.by_factor["discount_rate"] < 0          # a larger share given away lowers the contribution
    assert a.by_factor["checkout_rate"] == 0          # it did not change


def test_factors_that_scale_one_product_alike_get_equal_parts(graph):
    a = attribute(graph, "orders", changed(payment_success_rate=0.94 * 0.8, checkout_rate=0.5 * 0.8), BASE)
    assert a.by_factor["payment_success_rate"] == pytest.approx(a.by_factor["checkout_rate"], rel=1e-12)
    assert sum(a.by_factor.values()) == pytest.approx(a.actual - a.expected, rel=1e-12)


def test_a_factor_at_zero_falls_back_to_shapley_and_still_adds_up(graph):
    actual = changed(payment_success_rate=0.0, ad_load=4.0)   # a total outage: the logarithm is undefined
    a = attribute(graph, "contribution", actual, BASE)
    assert a.method == "shapley"
    assert sum(a.by_factor.values()) == pytest.approx(a.actual - a.expected, rel=1e-9)
    assert a.by_factor["payment_success_rate"] < 0 < a.by_factor["ad_load"]
    assert set(k for k, v in a.by_factor.items() if v != 0) == {"payment_success_rate", "ad_load"}


def test_shapley_and_lmdi_agree_on_who_matters(graph):
    actual = changed(payment_success_rate=0.70, ad_load=4.5)
    lmdi = attribute(graph, "contribution", actual, BASE)
    shap = attribute(graph, "contribution", actual, BASE, method="shapley")
    assert shap.method == "shapley"
    for k in ("payment_success_rate", "ad_load"):
        # the two factors sit in different products, so both methods must give the same split
        assert shap.by_factor[k] == pytest.approx(lmdi.by_factor[k], rel=1e-9)


def test_a_formula_that_is_not_a_sum_or_product_uses_shapley():
    specs = [spec_from_dict(d) for d in (
        {"name": "a", "description": "a", "unit": "count", "column": "a"},
        {"name": "b", "description": "b", "unit": "count", "column": "b"},
        {"name": "c", "description": "c", "unit": "count", "column": "c"},
        {"name": "y", "description": "y", "unit": "ratio", "formula": "a / b", "decomposition": "a / b"},
        {"name": "z", "description": "z", "unit": "count", "formula": "y * c", "decomposition": "y * c"},
    )]
    g = MetricGraph.from_specs(specs)
    assert expand(g, "z") is None
    a = attribute(g, "z", {"a": 30.0, "b": 10.0, "c": 5.0}, {"a": 20.0, "b": 10.0, "c": 4.0})
    assert a.method == "shapley"
    assert sum(a.by_factor.values()) == pytest.approx(15.0 - 8.0)
    assert a.by_factor["b"] == 0 and a.by_factor["a"] > 0 and a.by_factor["c"] > 0


def test_nothing_changed_gives_nothing(graph):
    a = attribute(graph, "contribution", BASE, BASE)
    assert a.actual == a.expected and all(v == 0 for v in a.by_factor.values())


def test_a_missing_value_gives_no_attribution(graph):
    assert attribute(graph, "contribution", changed(aov=math.nan), BASE) is None
