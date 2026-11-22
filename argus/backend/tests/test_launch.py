"""Enter a new country: per-user forecast, user count as input, and the 20-country hold-out backtest."""
import pytest
from fastapi.testclient import TestClient

from argus import config as cfg
from argus import launch as L
from argus.api import app


def test_launch_argentina_per_user_headline_and_assumed_users():
    r = L.launch("AR", 42)
    assert "AR" not in cfg.ISO_INDEX
    pu = r["margin_per_1000_users_day_usd"]
    assert 0 < pu["p10"] < pu["p50"] < pu["p90"]
    assert r["headline"] == f"Launching in Argentina: about ${pu['p50']:,.0f} margin per 1,000 users a day"
    u = r["users"]
    assert u["assumed"] is True and u["daily_users"] > 0 and u["source"].startswith("assumed")
    assert "(assumed)" in r["total_line"]
    day = r["margin_per_day_usd"]["p50"]                                    # total = per user × users × adoption
    assert day == pytest.approx(pu["p50"] / 1000 * u["daily_users"] * (1 - 2.718281828 ** -4), rel=0.02)
    weeks = [w["p50"] for w in r["margin_per_day_weekly_usd"]]
    assert len(weeks) == 4 and weeks == sorted(weeks)
    assert len(r["best_offer_mix"]) == 3 and {x["category"] for x in r["best_offer_mix"]} <= set(cfg.CATEGORIES)
    assert r["best_reward"]["reward_share"] in L.REWARD_GRID
    lf = r["learned_from"]
    assert len(lf) == 3 and abs(sum(x["weight"] for x in lf) - 1) < 0.02 and lf[0]["iso"] == "MX"
    assert r["learned_line"].startswith("Learned from Mexico")


def test_launch_users_input_scales_total_not_per_user():
    a, b = L.launch("AR", 42, users=100_000), L.launch("AR", 42, users=200_000)
    assert a["users"] == {**a["users"], "daily_users": 100_000, "assumed": False}
    assert a["margin_per_1000_users_day_usd"] == b["margin_per_1000_users_day_usd"]
    assert b["margin_per_day_usd"]["p50"] == pytest.approx(2 * a["margin_per_day_usd"]["p50"], rel=0.01)
    assert "(planned)" in a["total_line"]
    with pytest.raises(ValueError):
        L.launch("AR", 42, users=10)


def test_backtest_all_20_countries_reports_real_counts():
    bt = L.launch("AR", 42)["backtest"]
    assert bt["n"] == 20 and len(bt["countries"]) == 20
    for row in bt["countries"]:
        assert row["country"] not in row["learned_from"]                     # held out: never learns from itself
    o, m, rw = bt["offers"], bt["margin_per_user"], bt["reward"]
    assert o["true_top_in_top3"] == sum(r["offers"]["hit"] for r in bt["countries"])
    assert o["true_top_in_top3"] >= 18 and o["true_top_in_top3"] >= o["naive_baseline"]["true_top_in_top3"]
    assert o["true_top_is_first"] > o["naive_baseline"]["true_top_is_first"]   # similarity beats one global ranking
    assert m["median_abs_error"] <= 0.35 and m["inside_p10_p90"] >= 14         # calibrated range (~80% nominal)
    assert rw["within_one_step"] == sum(r["reward"]["within_one_step"] for r in bt["countries"]) and rw["within_one_step"] >= 16
    # the on-screen line: #1 offer hits, with the naive baseline only when it is clearly (>2) worse
    line = L.launch("AR", 42)["backtest_line"]
    top1, naive = o["true_top_is_first"], o["naive_baseline"]["true_top_is_first"]
    assert line.startswith(f"Tested on 20 countries we know: picked the #1 offer in {top1} of 20")
    assert (f"(the global average: {naive} of 20)" in line) == (top1 - naive > 2)
    assert L._tested_line({"n": 20, "offers": {"true_top_is_first": 15, "naive_baseline": {"true_top_is_first": 13}}}) \
        == "Tested on 20 countries we know: picked the #1 offer in 15 of 20"


def test_reward_truth_is_hidden_and_off_by_default():
    from argus.reality import Reality
    a, b = Reality(seed=3), Reality(seed=3)
    a.run(30)
    b.run(6)
    b.apply({"type": "set_reward", "country": cfg.ISO_INDEX["IN"], "reward_share": 0.65})
    b.run(24)
    i = cfg.ISO_INDEX["IN"]
    assert a.true[-1]["starts"][i].sum() < b.true[-1]["starts"][i].sum()     # higher rewards, more starts
    assert a.true[-1]["starts"][0].sum() == b.true[-1]["starts"][0].sum()     # other countries untouched


def test_launch_rejects_watched_or_unknown_country():
    with pytest.raises(ValueError):
        L.launch("MX")
    with pytest.raises(ValueError):
        L.launch("ZZ")


def test_launch_api():
    client = TestClient(app)
    h = {"X-Session": "launch-test"}
    d = client.post("/api/what_if/launch", json={"country": "AR"}, headers=h).json()
    for k in ("headline", "learned_line", "backtest_line", "range_line", "total_line", "margin_per_1000_users_day_usd",
              "users", "margin_per_day_usd", "best_offer_mix", "best_reward", "fraud_risk", "learned_from", "backtest"):
        assert k in d
    d2 = client.post("/api/what_if/launch", json={"country": "AR", "users": 250000}, headers=h).json()
    assert d2["users"]["assumed"] is False and d2["users"]["daily_users"] == 250000
    assert client.post("/api/what_if/launch", json={"country": "DE"}, headers=h).status_code == 400
