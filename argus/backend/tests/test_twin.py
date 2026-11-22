import numpy as np
import pytest

from argus import config as cfg
from argus.graph import MECHANISMS
from argus.reality import Reality
from argus.twin import KPIS, Twin

CA = cfg.ISO_INDEX["CA"]
USER_SIDE = ["new_accounts", "active_users", "impressions", "starts", "completions", "quality"]


@pytest.fixture(scope="module")
def world():
    r = Reality(seed=42)
    r.run(cfg.HISTORY_HOURS)
    tw = Twin(calendar=r.events)
    tw.learn(r.observed)
    return r, tw


def advance(r, tw, hours):
    for _ in range(hours):
        r.step()
        tw.observe(r.observed[-1])


def test_forecast_matches_history(world):
    r, tw = world
    obs = sum(x["revenue"].sum() for x in r.observed[-24:])
    fc = sum(tw.forecast(x["t"])["revenue"].sum() for x in r.observed[-24:])
    assert abs(obs / fc - 1) < 0.02


def test_twin_never_needs_hidden_state():
    import inspect
    import argus.twin as m
    text = inspect.getsource(m)
    for forbidden in ("hidden", "farms", "breaks", "_bot", "Reality"):
        assert forbidden not in text


def test_clean_week_has_no_alerts():
    r = Reality(seed=11)
    r.run(cfg.HISTORY_HOURS)
    tw = Twin(calendar=r.events)
    tw.learn(r.observed)
    consec = np.zeros((cfg.C, len(KPIS)))
    alerts = 0
    for _ in range(168):
        advance(r, tw, 1)
        hit = np.abs(tw.l1_z(r.t - 1)["z"]) >= 4
        consec = np.where(hit, consec + 1, 0)
        alerts += int((consec == 2).sum())
    assert alerts == 0


def test_bot_farm_root_shapley_and_counterfactual():
    r = Reality(seed=42)
    r.run(cfg.HISTORY_HOURS)
    tw = Twin(calendar=r.events)
    tw.learn(r.observed)
    r.add_bot_farm("CA", "Android")
    advance(r, tw, 6)
    t = r.t - 1
    z = dict(zip(KPIS, tw.l1_z(t)["z"][CA]))
    assert z["new_accounts"] > 4
    ts = [t - 2, t - 1, t]
    gp = tw.given_parents(ts)
    assert tw.z_of(gp["new_accounts"], CA, 1) > 4
    assert abs(tw.z_of(gp["revenue"], CA, 1)) < 3          # revenue is a symptom, not broken
    sh = tw.shapley(CA, 1, ts, "margin")
    assert sum(sh["phi"].values()) == pytest.approx(sh["v_all"] - sh["v_none"], rel=1e-6)
    user = sum(sh["phi"][m] for m in USER_SIDE)
    pricing = abs(sh["phi"]["revenue"] + sh["phi"]["rewards"])  # offer-mix noise moves both together
    assert user > 2 * pricing                               # user-side mechanisms drive the change
    share = tw.explained_share(CA, ts, {1: USER_SIDE}, "new_accounts")
    assert 0.8 <= share < 1.0                               # explains most of it, not a suspicious 100%
    assert tw.explained_share(CA, ts, {0: ["revenue"], 1: ["revenue"]}, "new_accounts") < 0.3


def test_chain_reproduces_observed_and_forecast(world):
    r, tw = world
    t = r.t - 1
    ts = [t - 2, t - 1, t]
    masks = np.array([[True] * len(MECHANISMS), [False] * len(MECHANISMS)])
    out = tw.chain(CA, ts, masks, platform=0)
    obs_ios = sum(tw.rows[x]["revenue"][CA, 0].sum() for x in ts)
    obs_and = sum(tw.rows[x]["revenue"][CA, 1].sum() for x in ts)
    fc_ios = sum(tw.forecast(x)["revenue"][CA, 0].sum() for x in ts)
    assert out["revenue"][0] == pytest.approx(obs_ios + obs_and, rel=1e-9)
    assert out["revenue"][1] == pytest.approx(fc_ios + obs_and, rel=1e-6)


def test_fast_forward_fraud_and_tracking():
    r = Reality(seed=42)
    r.run(cfg.HISTORY_HOURS)
    tw = Twin(calendar=r.events)
    tw.learn(r.observed)
    r.add_bot_farm("CA", "Android")
    advance(r, tw, 6)
    res = {x["id"]: x for x in tw.fast_forward(CA, ["none", "block_cluster", "verify_new_accounts", "cap_rewards"],
                                               {"kind": "fraud", "platform": 1, "categories": [1, 2], "elapsed": 6},
                                               r.t - 1)}
    assert res["none"]["delta_p50_usd"] == 0
    assert res["block_cluster"]["delta_p50_usd"] > 0
    assert len(res["block_cluster"]["p50"]) == 7
    assert all(a <= b <= c for a, b, c in zip(res["block_cluster"]["p10"], res["block_cluster"]["p50"],
                                              res["block_cluster"]["p90"]))
    gb = cfg.ISO_INDEX["GB"]
    r.add_tracking_break("GB", "iOS", [0, 1])
    advance(r, tw, 6)
    res = {x["id"]: x for x in tw.fast_forward(gb, ["none", "notify_advertiser", "pause_offers", "pause_notify"],
                                               {"kind": "tracking", "platform": 0, "categories": [0, 1], "elapsed": 6},
                                               r.t - 1)}
    assert res["pause_notify"]["delta_p50_usd"] > res["notify_advertiser"]["delta_p50_usd"] > 0
