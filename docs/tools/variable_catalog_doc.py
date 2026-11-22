"""Write docs/p0/VARIABLE_CATALOG.md from mkt/variable_catalog.py (single source of truth)."""
import collections
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from mkt.variable_catalog import CATALOG, EXCLUDED  # noqa: E402

NAMES = {"L0": "External environment", "L1": "Identity, scope, context", "L2": "Catalog, offers, competition", "L3": "Seller health",
         "L4": "Demand and discovery", "L5": "Transactions and fulfilment", "L6": "Trust and feedback", "L7": "Platform and infrastructure",
         "L9": "Observation and measurement"}
cnt = collections.Counter((v.kind, v.status) for v in CATALOG)
L = ["# Variable catalogue for the monitoring agent, by layer (generated from `mkt/variable_catalog.py`)", "",
     "Each variable is something the marketplace itself would log; datasets are the sources behind it. All four `Contracts` kinds "
     "are used (metric, state, event, categorical), and every entry builds into a valid `Contracts` definition "
     "(`tests/test_variable_catalog.py`).", "",
     "**Status:** *served* = the ports serve it today · *planned* = a verified dataset backs it, but the simulator layer is not built yet · "
     "*assumed* = no data, assumption-labelled lever.", "",
     "| | served | planned | assumed |", "|---|---|---|---|"]
for k in ("metric", "state", "event", "categorical"):
    L.append(f"| {k} | {cnt[(k, 'served')]} | {cnt[(k, 'planned')]} | {cnt[(k, 'assumed')]} |")
for layer, name in NAMES.items():
    rows = [v for v in CATALOG if v.layer == layer]
    if not rows:
        continue
    L += ["", f"## {layer} {name}", "", "| Variable | Kind | Entity | Meaning | Why the agent needs it | Source | Status |", "|---|---|---|---|---|---|---|"]
    for v in rows:
        f = v.spec.get("formula")
        meaning = v.meaning + (f" (= {f[0]} / {f[1]})" if f else "")
        L.append(f"| `{v.variable_id}` | {v.kind} | {v.entity} | {meaning} | {v.why} | {v.sources} | {v.status} |")
L += ["", "## Not variables (considered and excluded)", "", "| Layer | Field | Why not |", "|---|---|---|"]
L += [f"| {a} | {b} | {c} |" for a, b, c in EXCLUDED]
(ROOT / "docs/p0/VARIABLE_CATALOG.md").write_text("\n".join(L) + "\n")
print({k: v for k, v in sorted(cnt.items())}, len(CATALOG))
