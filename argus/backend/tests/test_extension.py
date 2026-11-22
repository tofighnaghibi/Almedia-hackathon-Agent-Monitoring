"""Automatic extension: place, validate (names, units, data, aggregation), handle ambiguity, monitor, explain."""
import pytest
from fastapi.testclient import TestClient

from argus.api import app
from argus.extension import DimensionError, dimension, resolve_formula, unit_text

client = TestClient(app)
KNOWN = {"new_accounts", "active_users", "impressions", "starts", "completions", "revenue", "rewards", "quality",
         "margin", "quality_per_completion"}


def test_units():
    assert unit_text(dimension("margin / new_accounts")) == "USD per new account"
    assert unit_text(dimension("revenue - rewards")) == "USD/h"
    assert unit_text(dimension("starts / impressions")) == "offer start per impression"
    with pytest.raises(DimensionError):
        dimension("revenue + completions")


def test_resolve_aliases_and_ambiguity():
    r = resolve_formula("margin / signups", KNOWN)
    assert r["expr"] == "margin / new_accounts" and ("signups", "new_accounts") in r["mapped"]
    assert resolve_formula("margin / users", KNOWN)["ambiguous"] == {"users": ["active_users", "new_accounts"]}
    assert resolve_formula("revnue / completions", KNOWN)["unknown"]["revnue"][0] == "revenue"


def post(name, **kw):
    return client.post("/api/metrics", json={"name": name, **kw}, headers={"X-Session": "extension"}).json()


def test_add_metric_api_end_to_end():
    client.post("/api/reset", headers={"X-Session": "extension"})
    r = post("margin_per_signup = margin / signups")
    assert r["status"] == "added" and r["validation"]["ok"]
    assert r["node"]["parents"] == ["margin", "new_accounts"] and r["node"]["edge"] == "formula"
    assert r["placement"]["breakdown"] == ["global", "country", "platform"]          # users have no offer category
    assert r["node"]["unit"] == "USD per new account" and r["node"]["aggregation"] == "ratio"
    assert r["note"].startswith("Added because") and r["monitoring"]["watched"]
    names = [c["name"] for c in r["validation"]["checks"]]
    assert names == ["formula", "names", "dimensions", "duplicate", "holds_on_data", "aggregation", "monitoring"]
    g = client.get("/api/graph", headers={"X-Session": "extension"}).json()
    assert {"source": "margin", "target": "margin_per_signup", "type": "formula"} in \
        [{k: e[k] for k in ("source", "target", "type")} for e in g["edges"]]
    st = client.get("/api/state", headers={"X-Session": "extension"}).json()
    assert st["added_metrics"][0]["id"] == "margin_per_signup" and st["attention"]["watched_series"] == 20 * 7

    amb = post("margin_per_user = margin / users")
    assert amb["status"] == "ambiguous" and amb["node"] is None
    assert amb["ambiguity"]["suggested_formulas"] == ["margin / active_users", "margin / new_accounts"]
    assert post("x = margin / weather")["status"] == "invalid"
    bad = post("bad = revenue + completions")
    assert bad["status"] == "invalid" and "units" in bad["message"]
    assert post("again = margin / new_accounts")["status"] == "duplicate"
    assert post("total_payout = rewards * 1")["status"] == "duplicate"       # same values as rewards on the data
    assert post("Start rate", formula="starts / impressions")["status"] == "added"
