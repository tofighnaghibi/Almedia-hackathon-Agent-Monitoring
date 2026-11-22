"""Generate docs/current-architecture.excalidraw: one overview of the architecture as it stands on
22 Nov 2026 (verified data foundation, pipeline, emulator, optional storefront, evaluation, open decisions)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from excal import arrow, base, box, lane, reset, save, text  # noqa: E402

reset(7)
OK, PART, DROP, NA, BUILT, PLAN, HOLD, PRIV, CORE = ("#d3f9d8", "#fff3bf", "#e9ecef", "#ffe3e3", "#b2f2bb", "#ffffff",
                                                     "#f1f3f5", "#ffc9c9", "#e7f5ff")

text("Marketplace emulator: architecture as it stands now (22 Nov 2026)", 0, -130, 2800, 30)
text("Green = verified / built · grey dashed = on hold · white = designed, not built. "
     "P0 is built and gated (S1–S8); transferred sources are added one at a time from P1.",
     0, -86, 2800, 16, color="#495057")

# 1. data foundation -------------------------------------------------------------------------------
lane("1  Data foundation: 19 datasets + 2 reference files", -20, -40, 560, 1180)
groups = [
    ("Backbone (real linked operations)\nOlist · Olist Funnel", OK),
    ("Shopping behaviour & devices\nRetailrocket · Taobao · ESCI · Google Analytics · Statcounter", OK),
    ("Experiments, ads, ranking (randomised)\nOpen Bandit · Criteo · Hillstrom · Expedia (random order)", OK),
    ("Reviews, transactions, prices\nCoat · UCI · M5 · Instacart", OK),
    ("Context\nBCB macro · holidays file · strike file (reference)", OK),
    ("Fraud & infrastructure shapes\nYelpChi · Amazon fraud graph · NAB/LogHub", OK),
    ("Support (not counted): Amazon Reviews 2023 category map only\n(ESCI title bridge; category files archived)", OK),
]
gy = 10
gboxes = []
for label, colour in groups:
    b = box(label, 0, gy, 520, colour, 15, minh=96 if label.count("\n") == 2 else 78,
            style="dashed" if colour in (DROP, NA) else "solid",
            stroke="#c92a2a" if colour == NA else "#1e1e1e")
    gboxes.append(b)
    gy += b["height"] + 22
text("Audit: 47 pass · 18 partial · 1 fail (historical).\nPer data type: 21 usable · 8 usable with a secondary · 2 caveats · 2 assumed\n"
     "(details: docs/stitching.excalidraw, docs/datasets/AUDIT.md)", 0, gy + 4, 520, 13, color="#495057")

# 2. pipeline ----------------------------------------------------------------------------------------
lane("2  Build pipeline", 600, -40, 440, 1180)
ds = box("scripts/datasets.py\nretrieve · fingerprint · audit\nDONE", 620, 40, 400, BUILT, 15)
st = box("scripts/stitch.py  (old all-at-once plan)\nSUPERSEDED, refuses to run\nreplaced by: one source at a time (phase 7)", 620, 230, 400, PLAN, 15, style="dashed")
cal = box("Calibration + re-anchoring\nlevels from Olist, shapes from the rest", 620, 420, 400, PLAN, 15)
mlib = box("Mechanism library\nfitted / transferred / bridged + explicit assumptions\neach with a transport card + trained_through date\n(assumed: fee-change response, payouts, logging faults)", 620, 590, 400, PLAN, 14, minh=110)
for g in gboxes[:7]:
    arrow(g, ds, "r", "l", color="#adb5bd")
arrow(ds, st, "b", "t", "verified data")
arrow(st, cal, "b", "t")
arrow(cal, mlib, "b", "t")

# 3. emulator + core --------------------------------------------------------------------------------
lane("3  Emulator (only business truth) + causal core", 1100, -40, 640, 1180)
gate = box("Action gateway  BUILT (P0)\nallowlist · bounds · rejects invalid · 3 levers", 1120, 40, 600, BUILT, 15)
kern = box("Simulator kernel  BUILT (P0 chain)\n1 h ticks + event queue · counterfactual twin · snapshot / restore\nrandomness keyed by (seed, mechanism, entity, event slot, tick)", 1120, 200, 600, PLAN, 15, minh=96)
led = box("Business ledger (event-sourced)", 1120, 360, 600, PLAN, 15)
obs = box("Observation layer  BUILT (P0)\ndelays · rounding · gaps · corrections as revisions", 1120, 470, 600, BUILT, 15)
port = box("Evidence qualification + v3 adapter\nObservation · ExperimentRecord · InterventionSpec · EvidenceRecord\nEnvironmentScope · CapabilitySpec · evidence-class firewall (no oracle)", 1120, 600, 600, CORE, 14, minh=96)
core = box("Causal core v3 (variable-first, domain-agnostic)\n→ findings, evidence gaps, identification certificates, action proposals", 1120, 760, 600, CORE, 15, minh=90)
arrow(mlib, kern, "r", "l", "mechanisms")
arrow(gate, kern, "b", "t"); arrow(kern, led, "b", "t"); arrow(led, obs, "b", "t"); arrow(obs, port, "b", "t", "observations")
arrow(port, core, "b", "t")
arrow(core, gate, "r", "r", "actions", "#7048e8")
text("Prototype code for parts of this lane exists, uncommitted (decision D8)", 1120, 880, 600, 13, color="#e67700")

# 4. astronomy shop ----------------------------------------------------------------------------------
lane("4  Astronomy Shop (optional, on hold)", 1800, -40, 520, 1180, bg="#f8f9fa")
shop = [("Session driver (replaces Locust)", HOLD), ("Catalog sync → Postgres", HOLD), ("sim-recommendation (same gRPC contract)", HOLD),
        ("Flag adapter → flagd (operational faults)", HOLD), ("Telemetry adapter + redaction → metrics", HOLD)]
sy = 40
sboxes = []
for label, colour in shop:
    sboxes.append(box(label, 1820, sy, 480, colour, 14, style="dashed"))
    sy += 80
real = box("Real shop services (pinned 7a88536)\nfrontend · catalog · cart · checkout · payment · shipping", 1820, sy + 20, 480, HOLD, 14, style="dashed")
arrow(kern, sboxes[0], "r", "l", "session plans", "#adb5bd", "dashed")
arrow(sboxes[0], real, "b", "t", color="#adb5bd", style="dashed")
arrow(sboxes[4], obs, "l", "r", "telemetry", "#adb5bd", "dashed")

# 5. privileged plane --------------------------------------------------------------------------------
lane("5  Privileged plane (evaluator only)", 600, 1180, 1720, 200, bg="#fff5f5")
truth = box("Truth store: latent state, true causes, twins", 620, 1240, 520, PRIV, 15)
ev = box("Evaluator: 3 separate reports, sealed scenarios", 1200, 1240, 520, PRIV, 15)
corr = box("Correlation table (storefront ↔ sim ids)", 1780, 1240, 500, PRIV, 15, style="dashed")
arrow(kern, truth, "b", "t", color="#e03131", style="dashed")
arrow(truth, ev, "r", "l", color="#e03131")
arrow(core, ev, "b", "t", "outputs only", "#495057", "dashed")
arrow(corr, ev, "l", "r", color="#e03131", style="dashed")

# decisions ------------------------------------------------------------------------------------------
X = 2380
lane("Decisions (first line = my recommendation)", X, -40, 640, 1420, bg="#fff9db")
decisions = [
    ("R1  Backbone", "**Keep Olist** (only real seller→order→delivery→review links).\nAlt: Amazon-centred (consistent English, no operations data)."),
    ("R2  Text language  ✔ DONE", "Field by field: reviews = Olist Portuguese, pooled by stars ×\ndelivery outcome (keeps text consistent with its cause);\ntitles + queries = ESCI English. Mixed-language shop, labelled."),
    ("R3  Dataset list  ✔ DONE", "19 datasets + 2 reference files; Amazon-M2, Tmall, iPinYou archived; Yahoo R3 + Keepa dropped.\nRule: one primary + at most one justified secondary per data type."),
    ("R4  Open Bandit BTS log  ✔ KEEP", "Kept (6.3 GB): a real off-policy-evaluation reference,\nvalid within its logged population and assumptions."),
    ("R5  English review text  ✔ DROPPED", "Not needed: review text comes from Olist (R2)."),
    ("R7  External input-layer review  ✔ ADOPTED", "v3 contracts · transport cards · trained_through cutoffs · entity/event-keyed\nrandomness · dependency checks, not just totals · phased P0 plan ·\ngates S1–S10. Fixed: survey answer ≠ seller reply; multi-seller mostly sequential."),
    ("R6  Audit-driven design fixes  ✔ DONE", "Position effect from Expedia (random order) instead of an assumption;\ndevices from Statcounter + Google Analytics; seller exit fitted on Olist;\nfailed/partial-only pairings removed."),
]
dy = 10
for head, body in decisions:
    text(head, X + 20, dy, 600, 18)
    b = box(body.replace("**", ""), X + 20, dy + 30, 600, "#ffffff", 13)
    dy += 30 + b["height"] + 18
text("Then, in order", X + 20, dy + 6, 600, 18)
for i, t in enumerate(["D7 ✔ approved · D8 ✔ reuse · P0 ✔ built, merged to main (S5 F2 exception accepted)",
                       "P1 now: session → exposure → cart → checkout layer, one source at a time (S4)",
                       "A2 ✔ approved: Astronomy Shop feasibility experiments (Docker)",
                       "A1 ✔ approved, after the session layer · A5: human sessions optional"]):
    box(t, X + 20, dy + 40 + i * 52, 600, HOLD, 13, style="dashed")

out = pathlib.Path(__file__).resolve().parents[1] / "current-architecture.excalidraw"
print(out, save(out), "elements")
