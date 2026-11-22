"""Phase 1 gate tests: S1 (semantic validity) and S2 (join & lineage) on real Olist data."""
import pathlib

import polars as pl
import pytest

from mkt import pipeline, registry, semantics


@pytest.fixture(scope="module")
def olist_src(cfg):
    try:
        return pathlib.Path(registry.load("olist", cfg.artifacts).path)
    except registry.MissingSource:
        pytest.skip("Olist not registered")


@pytest.fixture(scope="module")
def audit(olist_src):
    return semantics.audit(olist_src)


@pytest.fixture(scope="module")
def events(cfg, olist_src):
    return pl.read_parquet(pipeline.canonical_olist(cfg) / "events.parquet")


def test_gates_pass(audit):
    assert audit["gates"] == {"S1": "PASS", "S2": "PASS"}, [c for c in audit["checks"] if c["status"] == "FAIL"]


def test_every_exception_has_handling(audit):
    for c in audit["checks"]:
        if c["status"] == "EXCEPTION":
            assert c["action"], f"{c['id']} is an exception without documented handling"


def test_every_field_has_a_meaning_and_source(audit):
    for f in audit["fields"]:
        assert f["meaning"] and f["source"] in {"docs", "measured", "inferred"}


def test_review_event_is_the_customer_answer(events, cfg):
    rev = pl.read_parquet(pipeline.canonical_olist(cfg) / "reviews.parquet")
    sub = events.filter(pl.col("event_type") == "review_submitted")
    assert sub.height == rev.filter(pl.col("answered_at").is_not_null()).height
    assert set(sub["event_time"].to_list()) <= set(rev["answered_at"].to_list())


def test_no_invented_events(events):
    types = set(events["event_type"].unique().to_list())
    assert "payment_settled" not in types        # Olist has no settlement data
    assert "order_unavailable" not in types      # cancellation time does not exist in the source
    assert "review_created" not in types         # old, wrong name (it was the survey-sent date)


def test_items_keep_their_own_seller(events):
    items = events.filter(pl.col("event_type") == "order_item")
    assert items.height == semantics.EXPECTED_COUNTS["order_items"]
    assert items["seller_id"].null_count() == 0
    orders = events.filter(pl.col("event_type") == "order_created")
    assert orders.height == semantics.EXPECTED_COUNTS["orders"]
    assert orders["seller_id"].null_count() == orders.height   # order level is seller-agnostic


def test_status_is_a_snapshot_not_an_event_time(events):
    snap = events.filter(pl.col("event_type") == "order_status_snapshot")
    assert snap.height == semantics.EXPECTED_COUNTS["orders"]
    assert snap["event_time"].n_unique() == 1                 # all at the data cut
    assert set(snap["metric"].unique().to_list()) >= {"delivered", "canceled", "unavailable"}
