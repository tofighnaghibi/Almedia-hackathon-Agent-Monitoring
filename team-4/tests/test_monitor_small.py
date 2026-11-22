"""A small fault in a rate near 1: payment failures more than doubling (about 6 % -> 14 %) is only an
8.5 % fall of the success rate, under the 10 % floor on the rate itself. The floor is judged on the
smaller side, so the failure rate's own move counts (brain/monitor.py, COMPLEMENT_MIN_EFFECT), in the
hourly scan and in the watch after a release; the binomial test (p below 1e-7, 1e-4 after a release)
is unchanged.
"""
import shutil

import numpy as np
import pandas as pd
import pytest

from brain.data import Store
from brain.graph import MetricGraph
from brain.monitor import Config, Monitor, floor_moved, leading
from sim import db as simdb
from sim import inject
from sim.run import backfill
from tests.fixture_db import TS_FORMAT, make_fixture_db

T = pd.Timestamp
H = pd.Timedelta(hours=1)
FAULT_HOURS = 6

# the scan: a fixture market large enough that a 3-hour window of one cell answers at p below 1e-7
SCALE = 500                         # traffic x500: about 5,000-13,000 payment attempts an hour in DE x Android
FIXTURE_DAYS = 7                    # 2026-11-01 .. 2026-11-07
FIXTURE_FAULT = T("2026-11-06T12:00:00Z")
SUCCESS, FAULTY = 0.94, 0.86


def _small_fault(df: pd.DataFrame) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    hours = [(FIXTURE_FAULT + k * H).strftime(TS_FORMAT) for k in range(FAULT_HOURS)]
    m = (df["country"] == "DE") & (df["platform"] == "Android") & df["ts"].isin(hours)
    old_orders = df.loc[m, "orders"].to_numpy()
    df.loc[m, "payment_successes"] = rng.binomial(df.loc[m, "payment_attempts"].to_numpy(), FAULTY)
    df.loc[m, "orders"] = rng.binomial(df.loc[m, "payment_successes"].to_numpy(), 0.995)
    df.loc[m, "gmv"] = np.round(df.loc[m, "gmv"].to_numpy() * df.loc[m, "orders"].to_numpy()
                                / np.maximum(old_orders, 1), 2)
    return df


@pytest.fixture(scope="module")
def large_market(tmp_path_factory):
    d = tmp_path_factory.mktemp("small")
    return {name: make_fixture_db(d / f"{name}.db", days=FIXTURE_DAYS, seed=3, scale=SCALE, payment_success=SUCCESS,
                                  mutate=mutate)
            for name, mutate in (("clean", None), ("fault", _small_fault))}


def _payment_alert(monitor, hours):
    found = None
    for t in hours:
        tick = monitor.tick(as_of=t)
        found = next((a for a in leading(tick.alerts)
                      if a.metric == "payment_success_rate" and a.explained_by is None), None) or found
    return found


def test_small_payment_fault_is_raised_by_the_scan(large_market):
    monitor = Monitor(MetricGraph.load(), Store(large_market["fault"]))
    found = _payment_alert(monitor, [FIXTURE_FAULT + k * H for k in range(1, FAULT_HOURS + 1)])
    assert found is not None, "a payment success drop 0.94 -> 0.86 for 6 hours was never raised"
    assert found.trigger == "scan"
    assert found.segment == {"country": "DE", "platform": "Android"}
    assert found.expected > 0.93 and found.observed < 0.87
    assert found.onset == FIXTURE_FAULT
    # the reported effect is still observed / expected on the success rate itself, inside the 10 % floor
    assert -0.10 < found.effect < -0.07


def test_clean_large_market_raises_nothing(large_market):
    """At this volume p below 1e-7 is reached by small moves, so the floor is what keeps it quiet."""
    monitor = Monitor(MetricGraph.load(), Store(large_market["clean"]))
    raised = []
    for k in range(4 * 24, FIXTURE_DAYS * 24 + 1):
        tick = monitor.tick(as_of=T("2026-11-01T00:00:00Z") + k * H)
        raised += [(tick.now, a.metric, a.segment, round(a.score, 1)) for a in tick.alerts]
    assert raised == []


# the watch after a release: the simulator's market, where one cell is too thin for the scan
SIM_START = "2026-10-25T00:00:00Z"
SIM_DAYS = 10
SIM_FAULT = T("2026-11-03T08:00:00Z")


@pytest.fixture(scope="module")
def sim_db(tmp_path_factory):
    return backfill(str(tmp_path_factory.mktemp("small_sim") / "clean.db"), SIM_DAYS * 24, SIM_START, seed=11)


def test_small_payment_fault_is_raised_after_its_release(sim_db, tmp_path):
    path = tmp_path / "s.db"
    shutil.copy(sim_db, path)
    con = simdb.connect(path)
    try:
        inject.payment_failure(con, "DE", "Android", SIM_FAULT, severity=0.085, end=SIM_FAULT + FAULT_HOURS * H)
    finally:
        con.close()
    monitor = Monitor(MetricGraph.load(), Store(path))
    found = _payment_alert(monitor, [SIM_FAULT + k * H for k in range(1, FAULT_HOURS + 1)])
    assert found is not None, "the payments-sdk release was followed by a 0.94 -> 0.86 drop and nothing was raised"
    assert found.segment == {"country": "DE", "platform": "Android"}
    assert found.observed < found.expected and found.effect > -0.10


@pytest.mark.parametrize("observed, rate, passes", [
    (0.86, 0.94, True),     # failures 6 % -> 14 %: the rate fell 8.5 %, its complement rose 133 %
    (0.935, 0.94, False),   # failures 6 % -> 6.5 %: under the floor on either side
    (0.92, 0.94, False),    # failures 6 % -> 8 % (+33 %): no more than a shift in the mix can do
    (0.44, 0.50, True),     # at 0.5 the rate itself counts: 12 % down
    (0.455, 0.51, True),    # checkout_rate at 0.51 down 10.8 %: never judged more strictly than before
    (0.040, 0.050, True),   # below 0.5 the rate itself counts, as before
    (0.047, 0.050, False),
])
def test_floor_is_judged_on_the_smaller_side(observed, rate, passes):
    cfg = Config()
    moved = float(floor_moved(np.float64(observed), np.float64(rate), 0.0, "proportion", cfg))
    assert (abs(moved) >= 1.0) == passes
    assert np.sign(moved) == np.sign(observed - rate)
    # a ratio judged on its own spread keeps the floor on the rate itself
    assert abs(float(floor_moved(np.float64(observed), np.float64(rate), 0.0, "ratio", cfg))) \
        == pytest.approx(abs(observed / rate - 1) / cfg.min_effect)
