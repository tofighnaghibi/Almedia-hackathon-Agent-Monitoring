"""Generate docs/architecture.excalidraw from a small layout spec (rerun after edits)."""
import json, random, pathlib

random.seed(1)
E = []
def _id(): return "e%06d" % random.randrange(10**6)
def base(t, x, y, w, h, **k):
    d = dict(id=_id(), type=t, x=x, y=y, width=w, height=h, angle=0,
             strokeColor=k.get("stroke", "#1e1e1e"), backgroundColor=k.get("bg", "transparent"),
             fillStyle="solid", strokeWidth=k.get("sw", 2), strokeStyle=k.get("style", "solid"),
             roughness=1, opacity=k.get("opacity", 100), groupIds=[], frameId=None,
             roundness={"type": 3} if t == "rectangle" else None, seed=random.randrange(1, 2**31),
             version=1, versionNonce=random.randrange(1, 2**31), isDeleted=False,
             boundElements=[], updated=1, link=None, locked=False)
    E.append(d); return d
def text(s, x, y, w, size=16, align="left", container=None, color="#1e1e1e"):
    lines = s.split("\n"); h = len(lines) * size * 1.25
    t = base("text", x, y, w, h, stroke=color)
    t.update(text=s, originalText=s, fontSize=size, fontFamily=1, textAlign=align,
             verticalAlign="middle" if container else "top", containerId=container,
             lineHeight=1.25, autoResize=True, roundness=None)
    return t
def box(s, x, y, w, bg, size=16, stroke="#1e1e1e", style="solid", minh=0):
    h = max(minh, len(s.split("\n")) * size * 1.25 + 24)
    r = base("rectangle", x, y, w, h, bg=bg, stroke=stroke, style=style)
    t = text(s, x + 8, y + (h - len(s.split("\n")) * size * 1.25) / 2, w - 16, size, "center", r["id"])
    r["boundElements"].append({"type": "text", "id": t["id"]}); return r
def lane(title, x, y, w, h, bg):
    r = base("rectangle", x, y, w, h, bg=bg, stroke="#adb5bd", sw=1, style="dashed", opacity=60)
    text(title, x + 14, y + 8, w - 28, 20, color="#495057"); return r
def edge(a, b, side):
    cx, cy = a["x"] + a["width"] / 2, a["y"] + a["height"] / 2
    return {"r": (a["x"] + a["width"], cy), "l": (a["x"], cy),
            "t": (cx, a["y"]), "b": (cx, a["y"] + a["height"])}[side]
def arrow(a, b, sa="r", sb="l", label=None, color="#1e1e1e", style="solid"):
    (x1, y1), (x2, y2) = edge(a, b, sa), edge(b, a, sb)
    ar = base("arrow", x1, y1, x2 - x1, y2 - y1, stroke=color, style=style)
    ar.update(points=[[0, 0], [x2 - x1, y2 - y1]], lastCommittedPoint=None,
              startBinding={"elementId": a["id"], "focus": 0, "gap": 4},
              endBinding={"elementId": b["id"], "focus": 0, "gap": 4},
              startArrowhead=None, endArrowhead="arrow", roundness={"type": 2})
    a["boundElements"].append({"type": "arrow", "id": ar["id"]})
    b["boundElements"].append({"type": "arrow", "id": ar["id"]})
    if label:
        text(label, (x1 + x2) / 2 - 70, (y1 + y2) / 2 - 22, 140, 13, "center", color="#495057")
    return ar

REAL, CAL, SYN, PRIV, EXT, PROC = "#a5d8ff", "#ffec99", "#b2f2bb", "#ffc9c9", "#e9ecef", "#ffffff"

text("Marketplace reconstruction: architecture (D1–D6 decided; build paused until you approve it, D7)", -340, -110, 2000, 30)
text("Recommended: Olist spine + mechanisms transferred from other datasets + separate real-only tracks.  Full detail: docs/ARCHITECTURE.md",
     -340, -66, 2000, 18, color="#495057")

# Row 1: offline preparation
lane("1  Offline preparation   (minutes; cached, rerun only what changed)", -360, 0, 2140, 330, "#f8f9fa")
s1 = box("Olist  (spine)\nreal linked sellers, orders,\ndelivery, payments, reviews", -330, 50, 250, REAL, 14)
s2 = box("Calibration sources\nReviews 2023, ESCI, Retailrocket,\nOpen Bandit, Criteo, M5, ...", -330, 140, 250, CAL, 14)
s3 = box("External context\nholidays, BCB macro, weather,\n2018 truckers' strike", -330, 230, 250, EXT, 14)
reg = box("Source registry\nfiles + sha256 + licence", 0, 130, 200, PROC)
ad = box("Adapters + profiling\nonly place raw source\nIDs exist", 250, 120, 200, PROC)
can = box("Canonical store\nParquet + DuckDB", 500, 130, 200, PROC)
mt = box("Match registry\nexact join vs. entity\nresolution + confidence", 750, 120, 210, PROC)
mi = box("Interaction mining\nsurvival, GLMs, change-\npoints, randomised logs", 1010, 120, 220, PROC)
ca = box("Mechanism calibration\nparams + uncertainty,\nheld-out checks", 1280, 120, 220, CAL)
for s in (s1, s2, s3): arrow(s, reg)
for a, b in ((reg, ad), (ad, can), (can, mt), (mt, mi), (mi, ca)): arrow(a, b)

# Row 2: world generation
lane("2  World generation   (simulated marketplace; flows right to left)", -360, 370, 2140, 330, "#f8f9fa")
ml = box("Mechanism library\nM-01 … M-24, versioned", 1280, 430, 220, CAL)
sim = box("Discrete-time simulator\n1 h ticks, vectorised\nfactual run + counterfactual twin\nseeded RNG: identical reruns", 880, 420, 300, SYN)
sc = box("Scenario configs\ninterventions = do() on mechanisms", 880, 600, 300, EXT, 14)
we = box("World events\n(what really happened)", 560, 430, 220, SYN)
ob = box("Observation layer\nlogging gaps, delays,\nsampling, outages", 250, 420, 220, SYN)
st = box("Stitch stage  (§7.5)\nscripts/stitch.py\nsee stitching.excalidraw", 1540, 120, 220, "#d0bfff")
arrow(ca, st); arrow(st, ml, "b", "t", "shapes + anchored levels", "#7048e8"); arrow(ml, sim, "l", "r")
arrow(sc, sim, "t", "b"); arrow(sim, we, "l", "r"); arrow(we, ob, "l", "r")
arrow(can, sim, "b", "t", "seed entities", "#1971c2", "dashed")

# Row 3: replay and evaluation
lane("3  Replay & evaluation", -360, 740, 2140, 300, "#f8f9fa")
rt = box("Real-only tracks\nOlist replay, Retailrocket,\nOpen Bandit OPE, Criteo\n(from canonical store)", -330, 800, 250, REAL, 14)
os_ = box("Observable store\nevent_time + available_at", 0, 810, 220, PROC)
rp = box("Replayer\nonly serves events with\navailable_at <= clock", 300, 800, 230, PROC)
mon = box("Monitoring agent\n(external causal engine)", 610, 810, 230, "#d0bfff")
ev = box("Evaluator\nonset, attribution,\nabstention scoring", 1060, 800, 220, PRIV)
tr = box("Truth store\nlatent state, true causes,\neffects, identifiability", 1360, 800, 240, PRIV)
arrow(ob, os_, "b", "t"); arrow(rt, os_); arrow(os_, rp); arrow(rp, mon)
arrow(mon, ev, "r", "l", "alerts, explanations")
arrow(tr, ev, "l", "r")
arrow(sim, tr, "b", "t", "privileged truth", "#e03131", "dashed")
fence = base("rectangle", 1030, 770, 600, 190, stroke="#e03131", style="dashed", sw=2)
text("evaluator-only zone: replayer and monitor have no path in", 1040, 965, 580, 14, "center", color="#e03131")

# Legend
text("Legend", -340, 1070, 200, 18)
for i, (c, lab) in enumerate(((REAL, "real, linked data"), (CAL, "calibrated from other datasets"),
                              (SYN, "synthesised by mechanisms"), (PRIV, "privileged ground truth"),
                              (EXT, "config / context"))):
    base("rectangle", -340 + i * 330, 1105, 30, 24, bg=c)
    text(lab, -300 + i * 330, 1107, 280, 15)

# Decision panel
X = 1850
lane("Decisions  (D1–D6 taken 21 Nov 2026)", X, 0, 900, 1140, "#fff9db")
text("D1  Strategy: Olist-anchored + data stitching  ✔", X + 20, 50, 860, 22)
a = box("Olist-anchored  (recommended)\n\n+ real chain: on-time 4.29★ → >10 d late 1.71★\n+ real shocks: Black Friday 6.2×, 2018 strike\n   (promise 20 → 37 d, late rate fell to 0.4%)\n+ 12.8% of items in multi-seller products\n− small: 99,441 orders, 3,095 sellers, 2017–18\n− 96.9% one-time buyers; clicks, ads synthetic\n− Brazil; transfer from other markets assumed",
        X + 20, 90, 420, REAL, 15, minh=250)
b = box("Amazon-centred\n\n+ huge catalog, real review dynamics\n+ ESCI ∩ Reviews (US): 447,879 products\n   = 36.8% of ESCI-US  (measured)\n− M2 has no US; only 2–7% of M2 IDs in Reviews\n− no shared buyers, sellers, orders,\n   fulfilment: whole operational half invented\n− es / jp IDs match only 7.3% / 3.2%",
        X + 460, 90, 420, CAL, 15, minh=250)
text("Measured matches are product IDs only. Same ID ≠ same listing across countries (11,531 IDs span locales).",
     X + 20, 355, 860, 14, color="#495057")
items = [("D2  Licences  ✔", "Non-commercial research only; generated worlds with\nCC BY-NC-SA / competition data are not redistributed"),
         ("D3  Credentials  ✔", "Done: Kaggle + AIcrowd access in place. Official Amazon-M2 files\nare byte-identical to the Kaggle copy used for the measurements."),
         ("D4  Simulation step  ✔", "1 hour"),
         ("D5  Build budget  ✔", "4 hours max: core + stitching + scenarios S0, S1, S3, S4, S13 (§13.1)"),
         ("D6  Repo  ✔", "~/marketplace-recon under git"),
         ("Open: D7–D9", "D7 approve this architecture incl. stitching  ·  D8 keep / park / delete\nthe early prototype  ·  D9 optional downloads (M5, Instacart, UCI, YelpChi)")]
y = 415
for head, body in items:
    text(head, X + 20, y, 300, 20); r = box(body, X + 20, y + 32, 860, "#ffffff", 15); y += 32 + r["height"] + 22
text("Not yet verified: performance numbers (estimates); licences of M2, M5, Instacart.",
     X + 20, y + 4, 860, 14, color="#e03131")

import sys as _sys
_sys.path.insert(0, str(pathlib.Path(__file__).parent))
from excal import tech_legend  # noqa: E402
TECH = [
    ("Python package + CLI (mkt)", "One small installable program; each pipeline stage is a subcommand (ingest, fit, run, replay...). No servers or orchestration tools to operate."),
    ("Parquet", "Compressed, column-oriented file format. Every table is stored as Parquet so a query reads only the columns it needs."),
    ("DuckDB", "SQL engine that runs inside the Python process (like SQLite, but for analytics) and queries Parquet/CSV files directly. Replaces a database server."),
    ("NumPy / Polars", "Vectorised array and dataframe libraries. The simulator processes all shoppers in a tick at once instead of looping one by one, which makes it fast."),
    ("SHA-256 fingerprints", "A unique fingerprint of every input file and config. Detects when a source changed and proves which data version a result came from."),
    ("Content-addressed cache", "Each stage's output is stored under the fingerprint of its inputs + code. Same inputs = reuse the cached result, so reruns only redo what changed."),
    ("Exact join vs entity resolution", "Exact join links records by a real shared key. Entity resolution matches records without one (e.g. the same product ID string in two Amazon datasets) and records a confidence."),
    ("Survival models", "Model the time until something happens (delivery, repeat purchase) while correctly handling cases that haven't finished yet (censoring)."),
    ("GLMs", "Generalised linear models: regressions for probabilities and counts, e.g. P(1-star review | delivery was 4-10 days late)."),
    ("Change-point detection", "Finds the moment a time series changes behaviour, e.g. Black Friday 2017 or the 2018 truckers' strike."),
    ("Randomised logs / OPE", "Logs where actions were chosen at random with known probabilities (Open Bandit, Criteo). Off-policy evaluation (OPE) uses them to estimate another policy's value, valid only for the logged population, support and assumptions: a real reference, not universal ground truth."),
    ("Discrete-time simulation", "The world advances in 1-hour ticks; delayed events (deliveries, reviews, restocks) wait in a queue until their time. Simple and fast while keeping exact timestamps."),
    ("Seeded counter-based RNG (Philox)", "Randomness keyed by (seed, mechanism, entity, event slot, tick), so identical seeds give identical runs and an intervention that changes event counts does not shift other entities' draws. Updated 21 Nov (gate S7)."),
    ("Counterfactual twin", "The same world re-run with the same randomness but without the intervention. Factual minus twin = the true effect, known only to the evaluator."),
    ("do()-intervention", "Pearl's do-operator: forcing a lever (price, error rate, carrier speed) to a value, rather than just observing it. This is how scenarios inject causes."),
    ("event_time vs available_at", "When something happened vs. when a monitor could first see it. Replay only serves events with available_at <= clock, so there is no peeking into the future."),
    ("Observation layer", "A model of imperfect measurement (delays, dropped or duplicated events, sampling) applied between the true world and what the monitor sees."),
    ("Truth store / evaluator", "Privileged files (true causes, effects, latent state) readable only by the evaluator, which scores the monitoring agent's alerts against them."),
]
tech_legend(TECH, -340, 1200, cols=3, colw=1040, box_fn=box, text_fn=text)

out = pathlib.Path(__file__).resolve().parents[1] / "architecture.excalidraw"
out.write_text(json.dumps({"type": "excalidraw", "version": 2, "source": "https://excalidraw.com",
                           "elements": E, "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None},
                           "files": {}}, indent=1))
print(out, len(E), "elements")
