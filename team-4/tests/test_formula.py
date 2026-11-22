import math

import numpy as np
import pandas as pd
import pytest

from brain.formula import FormulaError, parse


def test_names_in_reading_order():
    assert parse("marketplace_fees + ad_spend - discounts").names == ("marketplace_fees", "ad_spend", "discounts")


@pytest.mark.parametrize("text, kind", [
    ("a + b - c", "sum"), ("a * b * c", "product"), ("a / b", "ratio"), ("a * b / c", "ratio"),
    ("a", "name"), ("(a + b) / c", "other"), ("2 * a", "other"),
])
def test_kind(text, kind):
    assert parse(text).kind == kind


def test_terms_factors_ratio_and_roles():
    assert parse("a + b - c").terms() == [(1, "a"), (1, "b"), (-1, "c")]
    assert parse("a - (b + c)").terms() == [(1, "a"), (-1, "b"), (-1, "c")]
    assert parse("x * y * z").factors() == ["x", "y", "z"]
    assert parse("a * b / c").ratio_parts() == (["a", "b"], ["c"])
    assert parse("a + b - c").roles() == {"a": "term+", "b": "term+", "c": "term-"}
    assert parse("a / b").roles() == {"a": "numerator", "b": "denominator"}


def test_evaluate_series_and_division_by_zero_gives_nan():
    f = parse("a / b")
    out = f.evaluate({"a": pd.Series([1.0, 2.0, 0.0]), "b": pd.Series([2.0, 0.0, 0.0])})
    assert out.iloc[0] == 0.5 and math.isnan(out.iloc[1]) and math.isnan(out.iloc[2])
    assert math.isnan(parse("a / b").evaluate({"a": 1.0, "b": 0.0}))
    arr = parse("a / b").evaluate({"a": np.array([1.0, 1.0]), "b": np.array([0.0, 4.0])})
    assert math.isnan(arr[0]) and arr[1] == 0.25


def test_evaluate_sum_with_unary_minus():
    assert parse("-a + b * 2").evaluate({"a": 3.0, "b": 5.0}) == 7.0


@pytest.mark.parametrize("bad", [
    "__import__('os').system('x')", "a.b", "a ** 2", "a < b", "'x'", "", "   ", "a[0]",
    "lambda: 1", "a if b else c", "True + a", "a +",
])
def test_rejects_unsafe_or_invalid(bad):
    with pytest.raises(FormulaError):
        parse(bad)


def test_unknown_name_at_evaluation():
    with pytest.raises(FormulaError):
        parse("a + b").evaluate({"a": 1.0})
