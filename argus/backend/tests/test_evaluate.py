from argus.evaluate import evaluate_seed, summarise


def test_harness_one_seed():
    row = evaluate_seed(42, clean_days=1)
    assert row["false_incidents_clean"] == [] and not row["promo_incident"] and row["promo_explained"]
    b = row["bot_farm"]
    assert b["detected"] and b["latency_h"] <= 4 and b["root"] == "new_accounts" and b["verdict"] == "supported"
    assert row["tracking_break"]["detected"]
    s = summarise([row])
    assert s["bot_farm_detected"] == "1/1" and s["false_incidents_clean_weeks"] == 0
