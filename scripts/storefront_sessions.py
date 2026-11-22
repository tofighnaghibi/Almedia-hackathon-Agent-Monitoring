"""Step 3 live: full sessions through the real Astronomy Shop (buying + browsing-only visits from the session layer).

Start the shop first (command in docs/p0/INTEGRATION_M1_M5.md), then:
    python -I scripts/storefront_sessions.py [--browse 300] [--buy 100]
"""
import argparse
import collections
import json
import pathlib
import shutil
import sys
from datetime import date, datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mkt import pipeline  # noqa: E402
from mkt.adapters.astronomy import storefront as sf  # noqa: E402
from mkt.config import Config  # noqa: E402
from mkt.emulator.api import Emulator, EmulatorConfig  # noqa: E402
from mkt.sim import chain  # noqa: E402
from mkt.sim.sessions import SessionParams  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--browse", type=int, default=300)
ap.add_argument("--buy", type=int, default=100)
a = ap.parse_args()
HOME = pathlib.Path.home()
run = HOME / "data/mkt/runs/storefront-sessions"
shutil.rmtree(run, ignore_errors=True)
canon = pipeline.canonical_olist(Config.load())
cat = sf.build_catalog(canon, HOME / "data/mkt/raw", n=200)
print("catalog rows synced:", sf.catalog_sync(cat))
people = json.load(open(HOME / ".cache/otel-demo-3.1.0/src/load-generator/people.json"))
driver = sf.SessionDriver(cat, run / "privileged/driver_results.jsonl", people[0], seed=11)
emu = Emulator(chain.fit(canon), run, session_params=SessionParams.from_cards())
emu.initialize(EmulatorConfig(start=date(2018, 1, 1), horizon_end=date(2018, 3, 1), sessions=True), seed=11)
emu.step(date(2018, 2, 20))
emu.start_storefront(datetime(2018, 2, 20), datetime(2018, 2, 21), max_sessions=a.buy, browse_sessions=a.browse)
emu.step(date(2018, 2, 21), storefront=driver)
c = collections.Counter((x.get("kind", "buy"), x["outcome"]) for x in emu.commands)
print("commands by kind and outcome:", dict(c))
print("checkouts sent:", driver.http_checkouts, "| results:", run / "privileged/driver_results.jsonl")
