"""M3: run the headless closed loop twice with the same seed and check the ledgers are identical."""
import json
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from mkt import pipeline  # noqa: E402
from mkt.config import Config  # noqa: E402
from mkt.harness import mvs  # noqa: E402
from mkt.sim import chain  # noqa: E402

base = pathlib.Path.home() / "data/mkt/runs/mvs"
p = chain.fit(pipeline.canonical_olist(Config.load()))
seeds = [int(x) for x in sys.argv[1:]] or [7]
out = []
for seed in seeds:
    reps = []
    for rep in (1, 2):
        d = base / f"seed{seed}-rep{rep}"
        shutil.rmtree(d, ignore_errors=True)
        reps.append(mvs.run(p, d, seed))
    r = reps[0]
    r["identical_ledgers"] = reps[0]["ledger_hash"] == reps[1]["ledger_hash"]
    r["identical_outputs"] = reps[0]["rev2_findings"] == reps[1]["rev2_findings"]
    out.append(r)
    print(json.dumps({k: r[k] for k in ("seed", "scenario_hidden", "rev1_outcome", "rev1_gaps", "granted", "verdict_target_week",
                                        "expected", "correct", "identical_ledgers", "identical_outputs")}))
pathlib.Path("docs/p0/mvs_headless.json").write_text(json.dumps(out, indent=1, default=str))
