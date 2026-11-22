"""Write the stitching table into ARCHITECTURE.md §7.5 from the same rows that draw docs/stitching.excalidraw
(single source of truth, so the doc and the diagram cannot drift). Run after editing the diagram rows."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
src = (ROOT / "docs/tools/make_stitching_excalidraw.py").read_text()
ns: dict = {}
exec(src[src.index("DS = {"):src.index("\n\n\ndef norm")], ns)
lines = ["| Layer | Data type | Method | Primary | Secondary (why) | What works / what doesn't |", "|---|---|---|---|---|---|"]
for layer, items in ns["ROWS"]:
    for dtype, method, prim, secs, note in items:
        sec = "; ".join(f"{s} ({why})" for s, why in secs) or "—"
        lines.append(f"| {layer.split()[0]} | {dtype} | {method} | {prim} | {sec} | {note} |")
table = "\n".join(lines)
doc = ROOT / "docs/ARCHITECTURE.md"
s = doc.read_text()
start, end = "<!-- stitching-table:start -->", "<!-- stitching-table:end -->"
block = f"{start}\n*Generated from `docs/tools/make_stitching_excalidraw.py` by `docs/tools/stitching_table.py`; do not edit by hand.*\n\n{table}\n{end}"
if start in s:
    s = re.sub(re.escape(start) + ".*?" + re.escape(end), block, s, flags=re.S)
else:
    raise SystemExit("markers not found in ARCHITECTURE.md")
doc.write_text(s)
print(f"wrote {len(lines) - 2} rows")
