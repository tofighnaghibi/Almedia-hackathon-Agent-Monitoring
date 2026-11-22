"""Write docs/p0/VARIABLE_TAXONOMY.md from mkt/taxonomy.py (single source of truth)."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from mkt import taxonomy as TX  # noqa: E402

L = ["# Variable taxonomy (generated from `mkt/taxonomy.py`; do not edit by hand)", "",
     "Conforms to `Contracts/` schema 2.0: each variable is one of the contract kinds, names its entity, and derived metrics "
     "are ratios with a closed `formula_ast`. Checked by `tests/test_contracts_conformance.py`.", "",
     "## Metrics", "", "| Variable | Entity | Aggregation | Type | Unit | Population | Formula | Identity tolerance | Meaning |",
     "|---|---|---|---|---|---|---|---|---|"]
for m in TX.METRICS:
    f = f"{m.formula[0]} / {m.formula[1]}" if m.formula else "—"
    L.append(f"| `{m.variable_id}` | {m.entity} | {m.aggregation} | {m.dtype} | {m.unit or '—'} | {m.population} ({m.coverage}) | {f} | "
             f"{m.identity_tolerance or '—'} | {m.description} |")
L += ["", "## Events", "", "| Variable | Entity | Engine event | Fields (identity first) | Meaning |", "|---|---|---|---|---|"]
for e in TX.EVENTS:
    L.append(f"| `{e.variable_id}` | {e.entity} | `{e.event_type}` | {', '.join(f'{k}: {t}' for k, (t, _) in e.fields.items())} | {e.description} |")
L += ["", "## Owner decisions applied (22 Nov 2026)",
      "- **G1:** variable catalogue = this taxonomy, served read-only through `definitions()`.",
      "- **G2:** replayed real history is `offline_replay`, not `production`; its evidence role is therefore `SANDBOX_OBSERVED`.",
      "- **G3:** transported parameters are carried in namespaced `extensions` (`mkt:transport_card`, `mkt:transport_status`): ok."]
(ROOT / "docs/p0/VARIABLE_TAXONOMY.md").write_text("\n".join(L) + "\n")
print("written", len(TX.METRICS), "metrics,", len(TX.EVENTS), "events")
