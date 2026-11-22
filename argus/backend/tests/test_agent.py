import inspect

import pytest

import argus.agent as agent_mod
from argus.agent import keyword_formula
from argus.graph import MECHANISMS
from argus.world import World

KNOWN = set(MECHANISMS) | {"margin", "quality_per_completion"}


@pytest.fixture()
def world():
    return World(seed=42)


def test_agent_reads_reality_only_through_the_view():
    src = inspect.getsource(agent_mod)
    for forbidden in (".hidden", ".farms", ".breaks", "_bot", ".exposure"):
        assert forbidden not in src
    assert src.count("self._reality") == 6  # __init__ + t, observed, calendar, sample_users, apply


def test_samples_have_no_labels(world):
    users = world.view.sample_users(1, 1, 50)
    assert all(not any(k.startswith("_") for k in u) for u in users)


def test_data_delay_waits_without_alert(world):
    world.step(2)
    world.inject("data_delay", "FR")
    world.step(1)
    fr = next(c for c in world.agent.state()["countries"] if c["iso"] == "FR")
    assert fr["status"] == "waiting"
    world.step(4)
    assert not world.agent.incidents
    assert any(e["country"] == "FR" and "Late data" in e["event"] for e in world.agent.explained)


def test_promo_is_explained_not_an_incident(world):
    world.inject("promo", "DE")
    world.step(8)
    assert not world.agent.incidents
    e = [x for x in world.agent.explained if x["country"] == "DE"]
    assert e and abs(e[0]["naive_z"]) >= 4 and abs(e[0]["twin_z"]) < 4


def test_tracking_break_root_is_revenue_without_l3(world):
    world.inject("tracking_break", "GB", "iOS")
    inc = world._step_until_incident("GB", 10)
    assert inc is not None
    assert inc["root"] == "revenue" and inc["l3"] is None and inc["kind"] == "tracking"
    assert inc["recommended"] in ("pause_notify", "notify_advertiser")
    with pytest.raises(ValueError):
        world.agent.approve(inc["id"], "block_cluster")


def test_keyword_formula():
    assert keyword_formula("x", "reward cost per completed offer = rewards / completions", KNOWN)[0] == \
        "rewards / completions"
    assert keyword_formula("Reward cost per completed offer", "", KNOWN)[0] == "rewards / completions"
    assert keyword_formula("Start rate", "how often users start an offer", KNOWN)[0] == "starts / impressions"
    assert keyword_formula("Revenue per active user", "", KNOWN)[0] == "revenue / active_users"
    assert keyword_formula("Weather", "temperature in Berlin", KNOWN)[0] is None


def test_add_metric_is_validated_and_monitored(world):
    out = world.agent.add_metric("Reward cost per completed offer", "rewards / completions per offer")
    assert out["validation"]["ok"], out
    node = out["node"]
    assert node["aggregation"] == "ratio" and node["parents"] == ["rewards", "completions"]
    assert world.agent.state()["attention"]["watched_series"] == 20 * 7
    dup = world.agent.add_metric("Reward cost again", "= rewards / completions")
    assert not dup["validation"]["ok"]
    bad = world.agent.add_metric("Bad", "= rewards / weather")
    assert not bad["validation"]["ok"]


def test_potential_is_a_believable_uplift(world):
    pot = world.agent.potential
    assert len(pot) == 20
    for p in pot:
        assert 2.0 <= p["potential_pct"] <= 5.0, p
        assert p["potential_usd_day"] > 0 and p["top_categories"] and p["explain"]
    st = world.agent.state()["countries"][0]
    assert st["potential_pct"] == pot[0]["potential_pct"]


def test_severity_reflects_risk_not_hourly_impact():
    from argus.agent import Agent
    assert Agent._severity(114_000, 21) == "high"     # bot farm: big 7-day risk, chargebacks within a day
    assert Agent._severity(76_000, 0) == "high"       # tracking break: losses accrue now
    assert Agent._severity(12_000, 20) == "high"      # moderate risk but advertisers react soon
    assert Agent._severity(12_000, None) == "medium"
    assert Agent._severity(300, None) == "low"        # small blip
    assert Agent._severity(0, None) == "low"


def test_state_exposes_impact_and_risk(world):
    world.inject("tracking_break", "GB", "iOS")
    world._step_until_incident("GB", 10)
    s = world.agent.state()["incidents"][0]
    assert {"impact_usd_h", "risk_7d_usd", "reaction_h", "severity"} <= set(s)


def test_potential_story_differs_by_region(world):
    pot = {p["iso"]: p for p in world.agent.potential}
    firsts = {p["top_categories"][0] for p in pot.values()}
    assert len(firsts) >= 2
    assert pot["US"]["top_categories"][0] != pot["BR"]["top_categories"][0]
    assert pot["KR"]["demand_index"]["Strategy games"] > 1.3        # strong strategy-games audience
    assert pot["BR"]["demand_index"]["Surveys"] > pot["DE"]["demand_index"]["Surveys"]


def test_issue_card_is_plain_language(world):
    world.inject("bot_farm", "CA", "Android")
    inc = world._step_until_incident("CA", 10)
    card = world.agent.card(inc)
    assert set(card) == {"title", "looks_like", "at_stake_7d_usd", "what_happened", "why", "what_to_do", "status_line"}
    text = " ".join(str(v) for v in card.values())
    for jargon in ("z=", "σ", "sigma", "Shapley", "mechanism", "L1", "L2", "L3"):
        assert jargon not in text, jargon
    assert "growth" in card["looks_like"] and "explained" in card["why"] and card["status_line"] == "Needs a decision"
    assert card["at_stake_7d_usd"] == inc["risk_7d_usd"] > 0
    world.agent.approve(inc["id"], inc["recommended"])
    assert world.agent.card(inc)["status_line"] == "Fix applied · verifying"
    world.step(25)
    assert "Prediction verified" in world.agent.card(inc)["status_line"] or \
        "prediction verified" in world.agent.card(inc)["status_line"]


def test_issue_states_and_no_duplicates():
    w = World(seed=42)
    w.demo("bot_farm")
    inc = w._latest("CA")
    assert w.agent.incident_summary(inc)["status_label"] == "Open"
    w.step(30)                                                # nobody acts: one issue, alerts merged into it
    ca = [i for i in w.agent.incidents if i["country"] == "CA"]
    assert len(ca) == 1
    s = w.agent.incident_summary(inc)
    assert s["status_label"] == "Still going" and s["alerts_merged"] >= 10 and s["first_seen"] == "Mon 04:00"
    # the advertisers' chargebacks (revenue per completion drops on iOS fintech/strategy) are the farm's predicted
    # downstream effect: attached to this issue, not a new one
    ds = s["downstream"]
    assert ds and all(d["effect"] == "chargeback" and d["category"] in ("Fintech apps", "Strategy games") for d in ds)
    assert all(abs(d["after_h"] - d["predicted_after_h"]) <= 6 for d in ds)


def test_issue_resolves_by_itself_when_the_problem_stops():
    w = World(seed=42)
    w.demo("bot_farm")
    inc = w._latest("CA")
    for farm in w.reality.farms:                              # test only: the farm stops without any fix in Argus
        farm.stopped = True
    w.step(12)
    s = w.agent.incident_summary(inc)
    assert s["status_label"] == "Resolved" and inc["applied"] is None
    assert "Recovered without action" in inc["trail"][-1]["finding"]
    w.inject("bot_farm", "CA", "Android")                     # it comes back: a new issue that links to the old one
    new = w._step_until_incident("CA", 10)
    assert new is not None and new["recurrence_of"] == inc["id"]


def test_different_causes_in_one_country_are_separate_issues():
    w = World(seed=42)
    w.demo("bot_farm")
    w.inject("tracking_break", "CA", "iOS")                   # a second, unrelated problem in the same country
    w.step(12)
    ca = [i for i in w.agent.incidents if i["country"] == "CA"]
    assert len(ca) == 2
    farm = next(i for i in ca if i["platform"] == "Android")
    brk = next(i for i in ca if i["platform"] == "iOS")
    assert farm["family"] == "users" and farm["root"] == "new_accounts"
    assert brk["family"] == "revenue" and brk["root"] == "revenue" and brk["kind"] == "tracking"
    assert farm["alerts_merged"] >= 10                       # the farm's repeated alerts still merge into one issue
    assert brk["hypotheses"][0]["verdict"] == "supported"


def test_found_line_and_explained_line():
    from argus.agent import Agent
    from argus.world import World

    base = {"detected_at": 5}                                     # hour 5 ends at 06:00
    assert Agent.found_line({**base, "reaction_h": 22}) == \
        "Found by Argus at 06:00, nobody asked · advertisers would react in ~22 h"
    assert Agent.found_line({**base, "reaction_h": 0}) == "Found by Argus at 06:00, nobody asked · losses are happening now"
    assert Agent.found_line({**base, "reaction_h": None}) == "Found by Argus at 06:00, nobody asked"
    w = World(seed=42)
    w.demo("bot_farm")
    st = w.agent.state()
    inc = st["incidents"][0]
    assert inc["found_line"].startswith("Found by Argus at 06:00, nobody asked")
    assert w.agent.incident_detail(w.agent.incidents[0])["found_line"] == inc["found_line"]
    de = [e for e in st["explained"] if e["country"] == "DE"][0]
    assert de["change_pct"] > 0 and de["line"] == f"Germany +{de['change_pct']}% · planned promo, no alarm"


def test_country_summary_block():
    from argus import config as cfg
    from argus.world import World

    w = World(seed=42)
    w.demo("bot_farm")
    ca = w.agent.country_detail(cfg.ISO_INDEX["CA"])["summary"]
    assert ca["status"] == "incident" and ca["verdict"] == "Fake accounts on Android" and "Canada" not in ca["verdict"]
    assert 2 <= len(ca["numbers"]) <= 3 and all({"label", "value", "usual", "unit", "line"} <= set(n) for n in ca["numbers"])
    assert len(ca["series"]["points"]) == 24 and all(p["lo"] <= p["usual"] <= p["hi"] for p in ca["series"]["points"])
    assert 1 <= len(ca["moving"]) <= 2 and ca["moving"][0]["platform"] == "Android" and ca["moving"][0]["kpi"] == "new_accounts"
    assert ca["moving"][0]["ratio"] > 1.3
    de = w.agent.country_detail(cfg.ISO_INDEX["DE"])["summary"]
    assert de["status"] == "explained" and de["verdict"].endswith("planned promo, no alarm") and de["moving"] == []
    us = w.agent.country_detail(cfg.ISO_INDEX["US"])["summary"]
    assert us == {**us, "status": "ok", "verdict": "All fine", "moving": []}


def test_attention_for_globe():
    from argus.world import World

    w = World(seed=42)
    w.demo("bot_farm")
    by = {c["iso"]: c for c in w.agent.state()["countries"]}
    assert 0.5 < by["CA"]["attention"] <= 1 and by["CA"]["attention_usd"] > 100_000
    assert by["DE"]["status"] == "explained" and by["DE"]["attention"] == 0           # planned promo never scores
    assert all(c["attention"] == 0 for c in by.values() if c["status"] == "ok")
    w.demo("approve")
    assert {c["iso"]: c for c in w.agent.state()["countries"]}["CA"]["attention"] == 0  # fix being verified


def test_growth_lines_plain_words():
    from argus.agent import Agent
    from argus.world import World

    w = World(seed=42)
    lines = [p["line"] for p in w.agent.potential] + [w.agent.growth_summary_line()]
    for p in w.agent.potential:
        assert 1 <= len(p["mostly_seen"]) <= 2 and set(p["mostly_seen_shares"]) == set(p["mostly_seen"])
    br = next(p for p in w.agent.potential if p["iso"] == "BR")
    assert br["line"].startswith("Brazil: users like casual games and surveys, but mostly see fintech apps.")
    assert Agent._growth_line("Japan", [1], [1, 2], 500).startswith("Japan: users like strategy games and already see some")
    for line in lines:
        assert not any(bad in line.lower() for bad in ("match", "taste", "feed", "potential"))


def test_short_title_and_detail():
    from argus.world import World

    w = World(seed=42)
    w.demo("tracking_break")
    inc = {i["country"]: i for i in w.agent.state()["incidents"]}
    assert inc["CA"]["short_title"] == "Fake accounts on Android in Canada" and inc["CA"]["title_detail"] is None
    assert inc["GB"]["short_title"] == "Advertisers stopped confirming offers in the UK"
    assert inc["GB"]["title_detail"] == "Casual-game and strategy-game offers on iOS"
    assert all(len(i["short_title"].split()) <= 7 for i in inc.values())
    d = w.agent.incident_detail(w.agent.incidents[-1])
    assert d["short_title"] == inc["GB"]["short_title"] and d["title"].startswith("Advertisers stopped confirming casual-game")


def test_replay_story_is_true_to_the_data():
    from argus.world import World

    w = World(seed=42)
    w.demo("tracking_break")
    farm, track = w.agent.incidents[0], w.agent.incidents[-1]
    r = w.agent.incident_detail(farm)["replay"]
    assert r["l1"]["sentence"] == "New sign-ups in Canada left their usual range at 04:00."     # margin never left
    left = r["l1"]["left_at"]["t"]
    pt = next(p for p in r["l1"]["series"] if p["t"] == left)
    assert not pt["lo"] <= pt["actual"] <= pt["hi"]
    assert r["l2"]["sentence"] == "It's Canada, Android." and any(c["driving"] for c in r["l2"]["cells"])
    steps = r["walk"]["steps"]
    assert steps[0]["node"] == "margin" and steps[-1] == {"node": "new_accounts", "label": "new sign-ups", "verdict": "cause"}
    assert r["walk"]["sentence"].endswith("new sign-ups: the cause.")
    assert r["l3"]["ran"] and r["l3"]["sentence"].startswith(f"{farm['l3']['summary']['n_flagged']} of 600 users checked look fake")
    assert r["proof"]["sentence"] == f"Without the fake accounts, {farm['hypotheses'][0]['explained']:.0%} of the change disappears."
    t = w.agent.replay(track)
    assert t["walk"]["sentence"] == "Margin moved → revenue: the cause." and not t["l3"]["ran"]
    assert t["l2"]["sentence"] == "It's the United Kingdom, iOS, casual-game and strategy-game offers."
    for part in (r, t):
        txt = " ".join(part[k]["sentence"] for k in ("l1", "l2", "walk", "l3", "proof")).lower()
        assert not any(j in txt for j in ("cluster", "segment", "kpi", "deviation", " z ", "σ"))


def test_country_series_marks_planned_promo():
    from argus import config as cfg
    from argus.world import World

    w = World(seed=42)
    w.demo("promo")
    ev = w.agent.country_detail(cfg.ISO_INDEX["DE"])["summary"]["series"]["events"]
    assert ev and ev[0]["label"] == "Planned promo" and ev[0]["kind"] == "start"
    assert w.agent.country_detail(cfg.ISO_INDEX["US"])["summary"]["series"]["events"] == []


def test_explained_country_series_shows_the_moved_kpi_against_a_normal_day():
    from argus import config as cfg
    from argus.world import World

    w = World(seed=42)
    w.demo("promo")
    s = w.agent.country_detail(cfg.ISO_INDEX["DE"])["summary"]
    se = s["series"]
    assert s["status"] == "explained" and se["kpi"] == "completions" and se["metric"] == "Completed offers per hour"
    pct = s["verdict"].split("%")[0]
    assert se["change_label"] == f"Completed offers {pct}% vs a normal Monday"
    last = se["points"][-1]
    assert last["value"] > last["normal"] * 1.05 and last["usual"] > last["normal"]       # the jump vs a normal day
    assert se["points"][0]["normal"] == se["points"][0]["usual"]                           # before the promo
    us = w.agent.country_detail(cfg.ISO_INDEX["US"])["summary"]["series"]
    assert us["kpi"] == "margin" and us["change_label"] is None and all("normal" in p for p in us["points"])
