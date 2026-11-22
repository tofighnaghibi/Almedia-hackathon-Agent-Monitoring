import json

import pandas as pd
import pytest

from simulator import SimConfig, generate
from simulator.__main__ import main
from simulator.pipeline import build_dataset, default_anchor

CFG = SimConfig(start="2026-10-05", days=14, seed=3)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("scenarios")
    result, truth = build_dataset(CFG, "all", out_dir=out)
    return out, result, truth


def test_writes_contract_files_and_ground_truth(built):
    out, result, truth = built
    for name in ["marketplace_hourly.csv", "events.csv", "ground_truth.json",
                 "inventory_daily.csv", "purchase_orders.csv"]:
        assert (out / name).exists(), name
    assert json.loads((out / "ground_truth.json").read_text()) == truth
    kinds = [s["kind"] for s in truth["scenarios"]]
    assert kinds.count("fault") == 4 and kinds.count("innocent") == 3
    events = pd.read_csv(out / "events.csv")
    assert len(events) == len(result.events) == 5
    assert events["start_time"].str.endswith("Z").all()


def test_scenarios_start_after_a_week_of_history(built):
    _, _, truth = built
    first = min(pd.Timestamp(s["start_time"]) for s in truth["scenarios"])
    assert first == default_anchor(CFG)
    assert first >= pd.Timestamp(CFG.start, tz="UTC") + pd.Timedelta(days=7)


def test_stock_out_comes_from_the_supply_chain(built):
    _, result, _ = built
    base = generate(CFG)
    po, po_base = result.world.purchase_orders, base.world.purchase_orders

    def late(p):
        e = p[p["category"] == "electronics"]
        return (e["actual_arrival"].isna() | (e["actual_arrival"] > e["expected_arrival"] + pd.Timedelta(days=2))).sum()

    assert late(po) > late(po_base)
    other = lambda r: r.world.inventory_daily.query("category != 'electronics'").reset_index(drop=True)  # noqa: E731
    pd.testing.assert_frame_equal(other(result), other(base))


def test_late_data_rows_are_missing(built):
    _, result, _ = built
    n_cells = len(CFG.countries) * len(CFG.platforms) * len(CFG.categories)
    assert len(result.marketplace_hourly) == CFG.days * 24 * n_cells - 4 * len(CFG.categories)


def test_none_gives_the_clean_baseline():
    result, truth = build_dataset(CFG, "none")
    assert truth == {"scenarios": []} and result.events.empty
    pd.testing.assert_frame_equal(result.marketplace_hourly, generate(CFG).marketplace_hourly)


def test_rejects_scenarios_outside_the_period():
    with pytest.raises(ValueError, match="after the simulated period"):
        build_dataset(CFG, "all", anchor="2026-10-17T10:00:00Z")
    with pytest.raises(ValueError, match="7 days of normal history"):
        build_dataset(CFG, "all", anchor="2026-10-06T10:00:00Z")


def test_cli(tmp_path, capsys):
    main(["--start", "2026-10-05", "--days", "14", "--seed", "3", "--scenarios", "faults", "--out", str(tmp_path)])
    printed = capsys.readouterr().out
    assert "injected 4 scenarios" in printed
    assert (tmp_path / "ground_truth.json").exists()


def test_stock_out_window_matches_what_happened(tmp_path):
    """The ground truth covers the days the stock-out is visible, including the
    recovery tail after the supplier delays stop, and nothing more."""
    from simulator.generator import build_world
    from simulator.pipeline import STOCK_OUT_EXCESS_THRESHOLD, observed_stock_out_days

    cfg = SimConfig(start="2026-10-05", days=28, seed=42)
    result, truth = build_dataset(cfg, "faults", anchor="2026-10-12T10:00:00Z", out_dir=tmp_path)
    s = next(x for x in truth["scenarios"] if x["scenario_id"] == "scn_electronics_stockout")
    start, end = pd.Timestamp(s["start_time"]), pd.Timestamp(s["end_time"])
    configured_end = pd.Timestamp("2026-10-12T10:00:00Z") + pd.Timedelta(days=6, hours=13)

    days = observed_stock_out_days(result.world, build_world(cfg).inventory_daily, "electronics")
    excess = days["excess"]
    assert excess[start.normalize()] > STOCK_OUT_EXCESS_THRESHOLD
    assert excess[end.normalize()] > STOCK_OUT_EXCESS_THRESHOLD
    assert (excess[excess.index > end] <= STOCK_OUT_EXCESS_THRESHOLD).all()  # recovered
    assert (excess[excess.index < start.normalize()] <= STOCK_OUT_EXCESS_THRESHOLD).all()
    assert end > configured_end  # shelves stay empty after the delays stop
    assert end < result.marketplace_hourly["timestamp"].max()  # recovery happens inside the data
    assert json.loads((tmp_path / "ground_truth.json").read_text()) == truth
