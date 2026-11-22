import json

import pytest
from fastapi.testclient import TestClient

from argus.api import app

client = TestClient(app)
H = {"X-Session": "api-test"}


def keys(d, expected):
    assert set(expected) <= set(d), f"missing {set(expected) - set(d)}"


def test_state_contract():
    r = client.post("/api/reset", headers=H)
    assert r.status_code == 200
    st = client.get("/api/state", headers=H).json()
    keys(st, ["clock", "countries", "incidents", "explained", "attention"])
    keys(st["clock"], ["t", "label", "utc_hour", "day_of_week", "day"])
    assert 0 <= st["clock"]["utc_hour"] <= 23 and st["clock"]["day_of_week"] in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    assert st["clock"]["utc_hour"] == (st["clock"]["t"] + 1) % 24        # clock = end of the last processed hour
    assert len(st["countries"]) == 20
    c = st["countries"][0]
    keys(c, ["iso", "name", "lat", "lng", "status", "health_z", "kpis", "potential_usd_day"])
    keys(c["kpis"], ["new_accounts", "completions", "revenue", "rewards", "margin", "quality_per_completion"])
    assert c["status"] in ("ok", "watch", "incident", "explained", "waiting")
    keys(st["attention"], ["watched_series", "investigations", "l3_runs"])


def test_sessions_are_isolated():
    client.post("/api/reset", headers={"X-Session": "a"})
    client.post("/api/reset", headers={"X-Session": "b"})
    client.post("/api/step", json={"hours": 5}, headers={"X-Session": "a"})
    ta = client.get("/api/state", headers={"X-Session": "a"}).json()["clock"]["t"]
    tb = client.get("/api/state", headers={"X-Session": "b"}).json()["clock"]["t"]
    assert ta == tb + 5


def test_full_flow_and_incident_contract():
    S = {"X-Session": "flow"}
    out = client.post("/api/demo/bot_farm", headers=S).json()
    inc_id = out["incident_id"]
    assert inc_id
    d = client.get(f"/api/incidents/{inc_id}", headers=S).json()
    keys(d, ["id", "title", "status", "country", "platform", "category", "detected_at", "trail", "graph",
             "contributions", "hypotheses", "l3", "fixes", "recommended", "applied", "verification", "reports"])
    keys(d["trail"][0], ["t", "level", "action", "finding"])
    keys(d["graph"], ["nodes", "edges"])
    keys(d["graph"]["nodes"][0], ["id", "label", "z", "state"])
    keys(d["graph"]["edges"][0], ["source", "target", "type"])
    keys(d["contributions"][0], ["mechanism", "value_usd"])
    keys(d["hypotheses"][0], ["name", "explained", "verdict"])
    keys(d["l3"], ["summary", "points"])
    keys(d["l3"]["summary"], ["n_users", "n_flagged", "coherence_flagged", "coherence_others", "top_devices"])
    keys(d["l3"]["points"][0], ["coherence", "payout_focus", "flagged"])
    f = d["fixes"][0]
    keys(f, ["id", "name", "description", "days", "p10", "p50", "p90", "delta_p50_usd", "advertiser_risk"])
    assert f["days"] == [1, 2, 3, 4, 5, 6, 7]
    keys(d["verification"], ["status", "predicted", "actual"])
    keys(d["reports"], ["ceo", "manager", "engineer"])
    assert client.post(f"/api/incidents/{inc_id}/approve", json={"fix_id": "nope"}, headers=S).status_code == 400
    a = client.post(f"/api/incidents/{inc_id}/approve", json={"fix_id": d["recommended"]}, headers=S).json()
    assert a["applied"] == d["recommended"] and a["status"] == "fix_applied"
    assert client.get("/api/incidents/inc-99", headers=S).status_code == 404
    assert client.get("/api/incidents", headers=S).json()[0]["id"] == inc_id


def test_other_endpoints():
    g = client.get("/api/graph", headers=H).json()
    keys(g, ["nodes", "edges", "exogenous", "levels"])
    c = client.get("/api/country/ca", headers=H).json()
    keys(c, ["iso", "name", "segments"])
    assert len(c["segments"]) == 12
    keys(c["segments"][0], ["platform", "category", "kpi_z", "deviation_usd_h"])
    keys(c["segments"][0]["kpi_z"], ["new_accounts", "completions", "revenue", "rewards", "quality_per_completion"])
    keys(c["platforms"][0], ["platform", "new_accounts_z", "active_users_z", "new_accounts_h", "new_accounts_expected_h"])
    assert client.get("/api/country/XX", headers=H).status_code == 404
    p = client.get("/api/potential", headers=H).json()
    keys(p["countries"][0], ["iso", "potential_usd_day", "top_categories", "explain"])
    r = client.post("/api/inject", json={"type": "bot_farm", "country": "CA", "platform": "android"}, headers=H)
    assert r.status_code == 200 and r.json()["ok"]
    assert client.post("/api/inject", json={"type": "meteor", "country": "CA"}, headers=H).status_code == 400
    assert client.post("/api/inject", json={"type": "promo", "country": "ZZ"}, headers=H).status_code == 400
    m = client.post("/api/metrics", json={"name": "Reward cost per completed offer",
                                          "description": "= rewards / completions"}, headers=H).json()
    keys(m, ["node", "validation"])
    assert m["validation"]["ok"] and m["node"]["id"] == "reward_cost_per_completed_offer"
    assert client.post("/api/step", json={"hours": 0}, headers=H).status_code == 400
    assert client.post("/api/demo/nope", headers=H).status_code == 404


def test_demo_scenes_are_deterministic_and_serialisable():
    outs = []
    for sess in ("d1", "d2"):
        S = {"X-Session": sess}
        for scene in ("start", "promo", "bot_farm", "investigate", "approve", "verify", "new_metric",
                      "tracking_break"):
            r = client.post(f"/api/demo/{scene}", headers=S)
            assert r.status_code == 200, (scene, r.text)
        outs.append(json.dumps(r.json(), sort_keys=True))
    assert outs[0] == outs[1]


def test_meta_lists_public_sources_and_calibration():
    m = client.get("/api/meta", headers=H).json()
    keys(m, ["data_sources", "calibration_note", "calibration"])
    keys(m["data_sources"][0], ["label", "value", "source", "url"])
    labels = [d["label"] for d in m["data_sources"]]
    assert "Annual revenue run rate" in labels and len([x for x in labels if x.startswith("iOS share")]) == 20
    assert all(d["url"].startswith("https://") for d in m["data_sources"])
    cal = m["calibration"]
    assert abs(cal["revenue_usd_day"]["sim"] / cal["revenue_usd_day"]["published"] - 1) < 0.1
    for row in cal["rewards_share"]:
        if row["published"] is not None:
            assert abs(row["sim"] - row["published"]) < 0.01, row


def test_sessions_evict_lru_and_expire_idle(monkeypatch):
    import argus.api as api
    monkeypatch.setattr(api, "MAX_SESSIONS", 2)
    pool = api.Sessions()
    a = pool.get("a")
    pool.get("b")
    assert pool.get("a") is a                 # touching "a" makes "b" the least recently used
    pool.get("c")                             # over the cap: evicts "b", not "a"
    assert len(pool) == 2 and pool.evicted == 1
    assert pool.get("a") is a
    monkeypatch.setattr(api, "SESSION_TTL", 0.0)
    pool.get("d")                             # everything idle longer than the TTL expires first
    assert pool.expired >= 1 and len(pool) == 1


def test_health_reports_sessions_and_memory():
    h = client.get("/api/health").json()
    keys(h, ["ok", "sessions", "max_sessions", "session_ttl_s", "rss_mb", "mem_budget_mb"])
    assert h["max_sessions"] >= 2 and h["session_ttl_s"] == 1800


def test_incident_series_normal_zone():
    S = {"X-Session": "series"}
    inc_id = client.post("/api/demo/bot_farm", headers=S).json()["incident_id"]
    d = client.get(f"/api/incidents/{inc_id}", headers=S).json()
    assert d["series_meta"]["kpi"] == "new_accounts" and "Android" in d["series_meta"]["label"]
    pts = d["series"]
    assert len(pts) == 48 and pts[-1]["t"] == client.get("/api/state", headers=S).json()["clock"]["t"]
    keys(pts[0], ["t", "label", "actual", "expected", "lo", "hi"])
    assert all(p["lo"] <= p["expected"] <= p["hi"] for p in pts)
    lz = d["left_zone_at"]
    assert lz is not None and lz["t"] <= d["detected_at"] and len(lz["label"]) == 9   # e.g. "Mon 08:00"
    before = [p for p in pts if p["t"] < lz["t"] - 1]
    assert sum(not p["lo"] <= p["actual"] <= p["hi"] for p in before) <= 3


def test_incident_counterfactual_removes_the_cause():
    S = {"X-Session": "counterfactual"}
    inc_id = client.post("/api/demo/bot_farm", headers=S).json()["incident_id"]
    d = client.get(f"/api/incidents/{inc_id}", headers=S).json()
    cf = d["counterfactual"]
    keys(cf, ["label", "series", "explained_share"])
    s = d["l3"]["summary"]
    assert cf["label"] == (f"Without the fake accounts ({s['n_flagged']} flagged in a sample of {s['n_users']}, "
                           f"about {s['estimated_total']} in total)")
    assert f"About {s['estimated_total']} fake accounts" in d["card"]["what_happened"]
    assert f"about {s['estimated_total']} in total" in d["hypotheses"][0]["name"]
    assert [p["t"] for p in cf["series"]] == [p["t"] for p in d["series"]]
    assert cf["explained_share"] == d["hypotheses"][0]["explained"] >= 0.8
    last_actual, last_cf = d["series"][-1], cf["series"][-1]
    assert last_cf["value"] < last_actual["actual"]                       # removing the fake accounts lowers sign-ups
    assert last_actual["lo"] <= last_cf["value"] <= last_actual["hi"]      # ... back into the normal zone


def test_fix_routes_and_do_nothing_events():
    S = {"X-Session": "routes"}
    inc_id = client.post("/api/demo/bot_farm", headers=S).json()["incident_id"]
    fixes = {f["id"]: f for f in client.get(f"/api/incidents/{inc_id}", headers=S).json()["fixes"]}
    for f in fixes.values():
        keys(f["delta_daily"], ["p10", "p50", "p90"])
        assert len(f["delta_daily"]["p50"]) == 7
        assert all(a <= b <= c for a, b, c in zip(*(f["delta_daily"][q] for q in ("p10", "p50", "p90"))))
    assert fixes["none"]["delta_daily"]["p50"] == [0] * 7
    assert fixes["verify_new_accounts"]["delta_daily"]["p50"][-1] == fixes["verify_new_accounts"]["delta_p50_usd"] \
        or abs(fixes["verify_new_accounts"]["delta_daily"]["p50"][-1] - fixes["verify_new_accounts"]["delta_p50_usd"]) < 0.2 * abs(fixes["verify_new_accounts"]["delta_p50_usd"])
    ev = fixes["none"]["events"]
    labels = [e["label"] for e in ev]
    assert labels == ["Advertisers start reclaiming money", "Advertiser cuts the budget"]
    assert 0 < ev[0]["after_h"] < ev[1]["after_h"] <= 168
    assert fixes["block_cluster"]["events"] == []


def test_l3_fake_account_picture():
    S = {"X-Session": "l3pic"}
    inc_id = client.post("/api/demo/bot_farm", headers=S).json()["incident_id"]
    l3 = client.get(f"/api/incidents/{inc_id}", headers=S).json()["l3"]
    keys(l3, ["summary", "points", "links", "groups"])
    keys(l3["points"][0], ["coherence", "payout_focus", "flagged", "device", "account_age_days"])
    pts = l3["points"]
    assert 0 < len(l3["links"]) <= 120
    for ln in l3["links"]:
        a, b = pts[ln["a"]], pts[ln["b"]]
        assert a["flagged"] and b["flagged"] and a["device"] == b["device"] and ln["a"] != ln["b"]
    assert sum(g["count"] for g in l3["groups"]) == sum(p["flagged"] for p in pts)
    assert l3["groups"][0]["count"] >= 5


def test_timeline_uses_only_what_argus_knows():
    S = {"X-Session": "timeline"}
    client.post("/api/demo/start", headers=S)
    client.post("/api/inject", json={"type": "data_delay", "country": "FR"}, headers=S)
    client.post("/api/step", json={"hours": 4}, headers=S)
    for scene in ("promo", "bot_farm", "approve", "verify"):
        client.post(f"/api/demo/{scene}", headers=S)
    ev = client.get("/api/timeline", headers=S).json()["events"]
    keys(ev[0], ["t", "label", "kind", "country"])
    kinds = [e["kind"] for e in ev]
    for k in ("waiting", "planned", "explained", "anomaly", "detected", "approved", "verified"):
        assert k in kinds, k
    assert [e["t"] for e in ev] == sorted(e["t"] for e in ev)
    ca = [e for e in ev if e["country"] == "CA"]
    assert [e["kind"] for e in ca] == ["anomaly", "detected", "approved", "verified"]
    assert all("farm" not in e["label"].lower() for e in ev)


def test_demo_story_clock():
    S = {"X-Session": "story"}
    st = client.post("/api/demo/promo", headers=S).json()["state"]
    assert st["clock"]["label"].endswith("Mon 03:00 UTC") and st["explained"]
    out = client.post("/api/demo/bot_farm", headers=S).json()
    d = client.get(f"/api/incidents/{out['incident_id']}", headers=S).json()
    assert d["left_zone_at"]["label"] in ("Mon 04:00", "Mon 05:00")
    assert out["state"]["clock"]["label"].endswith("Mon 06:00 UTC")
    ev = {e["kind"]: e["time_label"] for e in client.get("/api/timeline", headers=S).json()["events"]}
    assert ev["planned"] == "Mon 01:00" and ev["detected"] == "Mon 06:00"


def test_graph_edge_types_and_tree():
    S = {"X-Session": "graph"}
    client.post("/api/reset", headers=S)
    g = client.get("/api/graph", headers=S).json()
    assert {t["type"] for t in g["edge_types"]} == {"formula", "breakdown", "mechanism", "exogenous"}
    assert all(e["label"] for e in g["edges"]) and {e["type"] for e in g["edges"]} == {"formula", "mechanism", "exogenous"}
    node_ids = {n["id"] for n in g["nodes"]} | {"calendar"}
    assert all(e["source"] in node_ids and e["target"] in node_ids for e in g["edges"])   # edges stay drawable
    dims = {d["id"] for d in g["dimensions"]}
    assert all(e["type"] == "breakdown" and e["target"] in dims for e in g["breakdown_edges"])
    nodes = {n["id"]: n for n in g["nodes"]}
    assert nodes["new_accounts"]["breakdown"] == ["country", "platform"]
    assert nodes["margin"]["breakdown"] == ["country", "platform", "offer category"]
    t = client.get("/api/graph/tree", headers=S).json()
    assert t["roots"][:2] == ["margin", "quality_per_completion"]
    rows = t["rows"]
    assert rows[0]["id"] == "margin" and rows[0]["depth"] == 0 and rows[0]["relation"] is None
    assert {"formula", "mechanism"} <= {r["relation"] for r in rows}
    assert any(r["repeat"] for r in rows)                                                  # shared inputs shown once


def test_demo_frames_time_lapse():
    h = {"X-Session": "frames-test"}
    assert "frames" not in client.post("/api/demo/start", headers=h).json()
    client.post("/api/demo/promo", headers=h)
    d = client.post("/api/demo/bot_farm?frames=1", headers=h).json()
    fr = d["frames"]
    assert [f["t"] for f in fr] == list(range(fr[0]["t"], fr[0]["t"] + len(fr)))     # one per hour, oldest first
    assert fr[-1]["t"] == d["state"]["clock"]["t"] and fr[-1]["clock"] == d["state"]["clock"]
    ca = [next(c for c in f["countries"] if c["iso"] == "CA") for f in fr]
    assert [f["clock"]["utc_hour"] for f in fr] == [3, 4, 5, 6]
    assert [c["status"] for c in ca] == ["ok", "ok", "watch", "incident"]
    assert fr[-1]["incidents"][0]["country"] == "CA" and set(fr[-1]["countries"][0]) >= {"iso", "status", "attention"}
    client.post("/api/demo/approve", headers=h)
    v = client.post("/api/demo/verify?frames=1", headers=h).json()["frames"]
    assert len(v) >= 20 and v[-1]["incidents"][0]["status"] in ("verified", "resolved", "fix_applied")


def test_offer_mix_rollout_and_mix_columns():
    h = {"X-Session": "mix-test"}
    client.post("/api/demo/start", headers=h)
    pot = client.get("/api/potential", headers=h).json()["countries"]
    for p in pot:
        assert len(p["mix"]) == 6
        assert abs(sum(m["liked"] for m in p["mix"]) - 1) < 0.01 and abs(sum(m["seen"] for m in p["mix"]) - 1) < 0.01
    before = client.get("/api/state", headers=h).json()
    r = client.post("/api/what_if/offer_mix", json={"country": "US"}, headers=h).json()
    assert r["days"] == list(range(1, 8)) and len(r["current"]["p50"]) == 7 and len(r["better"]["p90"]) == 7
    g = r["gain_week_usd"]
    assert g["p10"] <= g["p50"] <= g["p90"] and g["p50"] > 0 and r["line"] == f"+${g['p50']:,} over the week"
    us = next(p for p in pot if p["iso"] == "US")["potential_usd_day"]
    assert abs(r["gain_day_usd"] - us) / us < 0.25                     # same model as the Growth number
    assert client.get("/api/state", headers=h).json() == before        # read-only
    assert client.post("/api/what_if/offer_mix", json={"country": "ZZ"}, headers=h).status_code == 400


def test_attention_counts_for_level_rail():
    h = {"X-Session": "rail-test"}
    a = client.post("/api/demo/start", headers=h).json()["state"]["attention"]
    assert a["segments_total"] == 240 and a["segments_opened"] == 0 and a["users_checked"] == 0
    assert a["users_total"] > 1_000_000
    a = client.post("/api/demo/tracking_break", headers=h).json()["state"]["attention"]
    assert a["segments_opened"] == 2 and a["users_checked"] == 600     # farm: 1 segment + 600 users; tracking: 1 segment


def test_country_unusual_words_and_ratios():
    h = {"X-Session": "unusual-test"}
    client.post("/api/demo/tracking_break", headers=h)
    gb = client.get("/api/country/GB", headers=h).json()
    s = gb["summary"]
    assert s["segments_total"] == 12 and s["unusual"]
    u = s["unusual"][0]
    assert u["line"] == "iOS · casual & strategy: revenue −86% vs usual" or u["line"].startswith("iOS · casual & strategy: revenue −")
    assert u["categories"] == ["Casual games", "Strategy games"] and u["view"] == "why" and u["ratio"] < 0.3
    seg = next(x for x in gb["segments"] if x["platform"] == "iOS" and x["category"] == "Casual games")
    assert set(seg["kpi_ratio"]) == set(seg["kpi_z"]) and seg["kpi_ratio"]["revenue"] < 0.3
    assert client.get("/api/country/US", headers=h).json()["summary"]["unusual"] == []
    h2 = {"X-Session": "unusual-test-2"}
    client.post("/api/demo/bot_farm", headers=h2)
    ca = client.get("/api/country/CA", headers=h2).json()["summary"]["unusual"]
    assert ca[0]["kpi"] == "new_accounts" and ca[0]["categories"] is None and ca[0]["view"] == "who"
    assert ca[0]["line"].startswith("Android · all offers: sign-ups ")
