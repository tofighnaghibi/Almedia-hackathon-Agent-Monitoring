"""End-to-end: the demo script from docs/HANDOFF.md section 3, driven through the agent."""
import json
import os

import pytest

from argus import config as cfg
from argus.world import World

NUMBERS: dict = {}


def status(world, iso):
    return next(c["status"] for c in world.agent.state()["countries"] if c["iso"] == iso)


@pytest.fixture(scope="module")
def demo():
    w = World(seed=42)
    w.step(2)
    assert all(c["status"] in ("ok", "watch") for c in w.agent.state()["countries"])

    # 2. planned promo: naive detector fires, Argus explains it
    w.inject("promo", "DE")
    w.step(6)
    promo_incidents = [i for i in w.agent.incidents if i["country"] == "DE"]

    # 3. bot farm
    w.inject("bot_farm", "CA", "Android")
    start = w.reality.t
    inc = w._step_until_incident("CA", 8)
    return {"w": w, "promo_incidents": promo_incidents, "inc": inc, "farm_start": start}


def test_promo_is_explained_not_an_incident(demo):
    w = demo["w"]
    assert demo["promo_incidents"] == []
    e = [x for x in w.agent.explained if x["country"] == "DE"]
    assert e, "expected an explained-change entry for DE"
    assert abs(e[0]["naive_z"]) >= 4 and abs(e[0]["twin_z"]) < 4
    NUMBERS["promo"] = {"naive_z": e[0]["naive_z"], "twin_z": e[0]["twin_z"]}


def test_bot_farm_detected_and_explained(demo):
    inc = demo["inc"]
    assert inc is not None, "bot farm not detected within 8 sim-hours"
    delay = inc["detected_at"] - demo["farm_start"] + 1
    assert delay <= 8
    assert inc["country"] == "CA" and inc["platform"] == "Android"
    assert inc["root"] == "new_accounts"
    assert inc["l3"] is not None and inc["l3"]["summary"]["n_flagged"] >= 5
    top = inc["hypotheses"][0]
    assert top["verdict"] == "supported" and top["explained"] >= 0.8 and top["name"].startswith("Fake accounts on Android")
    assert all(h["verdict"] == "rejected" for h in inc["hypotheses"][1:])
    assert inc["recommended"] != "none"
    assert inc["severity"] == "high" and inc["risk_7d_usd"] >= 25_000 and inc["reaction_h"] <= 48
    heat = demo["w"].agent.country_detail(cfg.ISO_INDEX["CA"])
    android = next(p for p in heat["platforms"] if p["platform"] == "Android")
    ios = next(p for p in heat["platforms"] if p["platform"] == "iOS")
    assert android["new_accounts_z"] > 4 and abs(ios["new_accounts_z"]) < 3
    fixes = {f["id"]: f for f in inc["fixes"]}
    NUMBERS["bot_farm"] = {
        "detection_delay_h": delay, "root": inc["root"], "explained_share": top["explained"],
        "l3": inc["l3"]["summary"], "recommended": inc["recommended"],
        "fix_delta_p50_usd_7d": {k: f["delta_p50_usd"] for k, f in fixes.items()},
        "impact_usd_h": inc["impact_usd_h"], "risk_7d_usd": inc["risk_7d_usd"], "reaction_h": inc["reaction_h"],
        "severity": inc["severity"],
    }


def test_approve_heals_canada_and_verifies(demo):
    w, inc = demo["w"], demo["inc"]
    w.agent.approve(inc["id"], inc["recommended"])
    healthy_after = None
    for h in range(1, 26):
        w.step(1)
        if healthy_after is None and status(w, "CA") == "ok":
            healthy_after = h
    assert healthy_after is not None and healthy_after <= 24
    assert status(w, "CA") == "ok"
    v = inc["verification"]
    assert v["status"] in ("verified", "missed") and v["actual"] is not None
    assert v["status"] == "verified"
    NUMBERS["approve"] = {"healthy_after_h": healthy_after, "verification": v}


def test_new_metric_and_tracking_break(demo):
    w = demo["w"]
    m = w.agent.add_metric("Reward cost per completed offer", "reward cost per completed offer = rewards / completions")
    assert m["validation"]["ok"] and m["node"]["aggregation"] == "ratio"
    w.inject("tracking_break", "GB", "iOS")
    start = w.reality.t
    inc = w._step_until_incident("GB", 8)
    assert inc is not None and inc["root"] == "revenue" and inc["l3"] is None
    assert inc["hypotheses"][0]["name"].startswith("Advertisers' tracking broke") and inc["hypotheses"][0]["verdict"] == "supported"
    assert inc["severity"] == "high"
    NUMBERS["tracking_break"] = {"detection_delay_h": inc["detected_at"] - start + 1,
                                 "explained_share": inc["hypotheses"][0]["explained"],
                                 "recommended": inc["recommended"], "severity": inc["severity"],
                                 "impact_usd_h": inc["impact_usd_h"], "risk_7d_usd": inc["risk_7d_usd"],
                                 "fix_delta_p50_usd_7d": {f["id"]: f["delta_p50_usd"] for f in inc["fixes"]}}
    others = [i for i in w.agent.incidents if i["country"] not in ("CA", "GB")]
    assert others == [], [i["title"] for i in others]


@pytest.mark.parametrize("seed", [42, 7])
def test_no_false_incidents_in_seven_clean_days(seed):
    w = World(seed=seed)
    w.step(7 * 24)
    assert w.agent.incidents == [], [i["title"] for i in w.agent.incidents]
    NUMBERS.setdefault("clean_week_incidents", {})[seed] = 0


def teardown_module(module):
    path = os.environ.get("ARGUS_E2E_NUMBERS")
    if path:
        with open(path, "w") as f:
            json.dump(NUMBERS, f, indent=1, default=str)
