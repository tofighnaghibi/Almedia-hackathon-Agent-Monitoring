"""docs/martha-architecture.excalidraw: the system at a glance, for the README (editable in Excalidraw)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from excal import arrow, box, lane, reset, save, text  # noqa: E402

reset(21)
DATA, SIM, SHOP, CORE, AGENT, HUMAN, PRIV = "#e7f5ff", "#d3f9d8", "#fff4e6", "#e5dbff", "#d0ebff", "#ebfbee", "#ffe3e3"
text("Marketplace Martha: how it works", 0, -110, 2200, 34)
text("MARketplace Tracing through causal Hindsight & Adaptation", 0, -60, 2200, 20, color="#495057")

lane("1  Real data", -20, -10, 420, 620)
d1 = box("Olist (Brazil)\nreal orders · delivery · reviews\nsets the levels", 0, 40, 380, DATA, 17, minh=110)
d2 = box("18 public datasets\nfunnels · search positions · devices\nprices · stock · ads · returns · fraud", 0, 210, 380, DATA, 17, minh=110)
d3 = box("Stitching\n33 transport cards lend the shapes,\neach held-out checked", 0, 380, 380, DATA, 17, minh=110)
arrow(d2, d3, "b", "t")

lane("2  The marketplace world", 460, -10, 460, 620)
s1 = box("Emulator\norders · sessions · carts · checkout\ndelivery · reviews · counterfactual twin\n+ what a monitor really sees:\ndelays · gaps · late corrections", 480, 40, 420, SIM, 17, minh=190)
s3 = box("Astronomy Shop (real services)\nemulator shoppers buy through it\nreal faults · real telemetry (redacted)", 480, 340, 420, SHOP, 17, minh=130)
arrow(d1, s1, "r", "l", "levels"); arrow(d3, s1, "r", "l", "shapes"); arrow(s1, s3, "b", "t", "drives shoppers")

lane("3  Martha", 960, -10, 760, 620)
p1 = box("Read-only contract ports\ntyped evidence · provenance · data cut", 980, 40, 720, CORE, 17, minh=90)
c1 = box("Causal reasoning engine\nframe → audit inputs → select methods → acquire evidence\n→ evaluate → challenge → report", 980, 170, 720, CORE, 17, minh=110)
a1 = box("Agent\nwatches continuously · traces alarms to their cause\nasks for evidence · proposes a fix + expected effect", 980, 320, 720, AGENT, 17, minh=110)
g1 = box("Policy gate: the engine cannot act", 980, 470, 720, "#ffffff", 17, minh=60, stroke="#c92a2a")
arrow(s1, p1, "r", "l", "evidence"); arrow(s3, p1, "r", "l", "telemetry")
arrow(p1, c1, "b", "t"); arrow(c1, a1, "b", "t"); arrow(a1, g1, "b", "t")

lane("4  Action, with a person", 1760, -10, 420, 620)
h1 = box("A person approves\none action at a time", 1780, 300, 380, HUMAN, 17, minh=90, stroke="#2b8a3e")
x1 = box("Sandbox executor\nfixes the shop's flags (local only)\nand reads the change back", 1780, 440, 380, HUMAN, 17, minh=110)
arrow(g1, h1, "r", "l", "proposal"); arrow(h1, x1, "b", "t")

lane("Kept apart (evaluator only)", 460, 660, 1720, 130, bg="#fff5f5")
t1 = box("Sealed ground truth: real causes, twins, answer keys — never visible to Martha; used only to score her", 480, 700, 1680, PRIV, 16, minh=60)
out = pathlib.Path(__file__).resolve().parents[1] / "martha-architecture.excalidraw"
print(out, save(out), "elements")
