"""Generate docs/stitch-pipeline.excalidraw: the stitch stage as built (ARCHITECTURE.md §7.5-7.6).

Top: the per-row pipeline that scripts/stitch.py runs. Bottom: one box per stitching row (mkt/stitch/plan.py),
coloured by the status in the latest run manifest (docs/stitch/manifest.json), if a run exists.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(ROOT))
from excal import arrow, box, lane, reset, save, text  # noqa: E402

from mkt.stitch.plan import PLAN  # noqa: E402

reset(11)
man_path = ROOT / "docs/stitch/manifest.json"
man = json.loads(man_path.read_text()) if man_path.exists() else {"cards": {}}
COL = {"supported": "#b2f2bb", "partially supported": "#d3f9d8", "unsupported transfer": "#ffec99", "joined": "#d0ebff",
       "assumed": "#e9ecef", "evidence only": "#fff3bf", "failed": "#ffc9c9", None: "#ffffff"}

text("Stitch stage: how every dataset becomes a transport card or bridge table (scripts/stitch.py)", 0, -120, 2600, 30)
text(f"Status from the latest run: {man.get('run_at', 'not run yet')} · {man.get('wall_seconds', '—')} s · "
     f"{len(man['cards'])}/{len(PLAN)} rows", 0, -78, 2600, 16, color="#495057")

lane("Per-row pipeline (one entry per stitching row; rows run in parallel, cached by content)", -20, -40, 2620, 330)
stages = [
    ("1  Inputs on disk\n~/data/mkt/raw · fingerprinted\nslow formats cached once\n(xlsx → parquet, 1.2 GB JSON → mmap scan)", "#e7f5ff"),
    ("2  Fitter (§7.5 method)\nfit · transfer · bridge · pool\njoin on date · anchor · assumed\nDuckDB column scans, no full loads", "#e7f5ff"),
    ("3  Held-out check (§7.6)\nchronological halves where time exists,\nrandom halves otherwise\n→ status", "#fff3bf"),
    ("4  Transport card / bridge table\nestimate + 95% CI · trained_through\nsource vs target population\nassumptions · status (never silent)", "#d3f9d8"),
    ("5  Mechanism library\ncards read by simulator layers\nconsumed now: chain, sessions, telemetry\nothers: fitted, waiting for their layer", "#f3f0ff"),
    ("6  Gates when a layer consumes it\nfidelity F · invariance V (S7)\nrealism S5 · value S10\npreregistered before measuring", "#ffe3e3"),
]
prev = None
for i, (label, bg) in enumerate(stages):
    b = box(label, i * 432, 10, 400, bg, 14, minh=150)
    if prev:
        arrow(prev, b, "r", "l")
    prev = b
text("Outputs: docs/stitch/cards/<row>.json (small, versioned) · ~/data/mkt/stitch/bridges/*.parquet (large) · docs/stitch/manifest.json "
     "(input fingerprints, fitter hash, status, timings) · docs/stitch/STITCH_REPORT.md", 0, 200, 2600, 14, color="#495057")
text("Status colours:  supported (fitted on the target)  ·  partially supported (transferred; held-out stable)  ·  unsupported transfer (held-out unstable: "
     "kept with a sensitivity range)  ·  joined (exogenous series)  ·  evidence only  ·  assumed  ·  failed", 0, 232, 2600, 14, color="#495057")

lane("Stitching rows (mkt/stitch/plan.py) and their latest status", -20, 320, 2620, 1100)
layers = sorted({e.layer for e in PLAN}, key=lambda s: int(s[1:]))
x0, y = 0, 370
for lay in layers:
    rows = [e for e in PLAN if e.layer == lay]
    text(lay, x0, y + 10, 60, 22)
    for j, e in enumerate(rows):
        c = man["cards"].get(e.id, {})
        st = c.get("status")
        key = next((k for k in COL if k and st and st.startswith(k)), None) if st else None
        line2 = f"{e.method} · {', '.join(e.sources) or '—'}"
        line3 = (f"{st[:40]} · {c.get('seconds', '?')} s" if st else "not run") + ("  ·  USED BY " + e.consumed_by if e.consumed_by else "")
        box(f"{e.data_type}\n{line2}\n{line3}", 80 + (j % 4) * 632, y + (j // 4) * 112, 612, COL[key], 12, minh=96,
            style="solid" if e.consumed_by else "dashed")
    y += ((len(rows) + 3) // 4) * 112 + 18
text("Solid border = the simulator already consumes this card · dashed = fitted only (its simulator layer is not built yet)", 0, y + 6, 2600, 14,
     color="#495057")
out = ROOT / "docs/stitch-pipeline.excalidraw"
print(out, save(out), "elements")
