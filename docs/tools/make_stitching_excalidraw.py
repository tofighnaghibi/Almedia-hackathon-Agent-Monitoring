"""Generate docs/stitching.excalidraw: primary/secondary source per data type, WITH the audit result of
every dataset in every row (read from docs/datasets/*.json written by scripts/datasets.py)."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from excal import base, box, reset, save, tech_legend, text  # noqa: E402

DOCS = pathlib.Path(__file__).resolve().parents[1]
reset(4)

# diagram label -> (audit key, colour)
DS = {
    "Olist": ("olist", "#a5d8ff"), "Olist Funnel": ("olist_funnel", "#74c0fc"), "Retailrocket": ("retailrocket", "#ffd8a8"),
    "Taobao": ("taobao", "#ffe3e3"), "ESCI": ("esci", "#d0bfff"), "Open Bandit": ("open_bandit", "#b2f2bb"),
    "Criteo": ("criteo", "#96f2d7"), "Hillstrom": ("hillstrom", "#ebfbee"), "Coat": ("coat", "#fff3bf"), "UCI": ("uci", "#dee2e6"),
    "M5": ("m5", "#ffc9c9"), "Instacart": ("instacart", "#fcc2d7"), "BCB SGS": ("bcb", "#c0eb75"), "Holidays": ("holidays", "#d3f9d8"),
    "Public record": ("public_record", "#f1f3f5"), "YelpChi": ("yelpchi", "#ffe8cc"), "Amazon fraud": ("amazon_fraud", "#e5dbff"),
    "NAB / LogHub": ("nab_loghub", "#e7f5ff"), "Expedia": ("expedia", "#d8f5a2"), "Google Analytics": ("ga", "#fde2e4"),
    "Statcounter": ("statcounter", "#e3fafc"),
    # support (not counted among the 18)
    "Reviews 2023": ("reviews2023", "#eebefa"), "Astronomy Shop": ("astronomy_shop", "#99e9f2"),
    "Assumed": (None, "#ffffff"),
}
REFERENCE = {"Holidays": "holidays_br_2016_2018.csv (39 dates, rule-generated, verified)", "Public record": "truckers_strike_2018.json (window + sources)"}
SUPPORT = {"Reviews 2023": "category map for the ESCI bridge only (R2: reviews come from Olist)",
           "Astronomy Shop": "not a dataset: optional live storefront, on hold (A1–A5)"}
DROPPED = [("Amazon-M2", "Taobao covers sessions with real timestamps"), ("Tmall", "repeat buys covered by Olist + Instacart"),
           ("iPinYou", "auction prices only; no auction in the build"), ("Yahoo! R3", "unreachable; Coat covers it"),
           ("Keepa", "paid")]
LANG = {"olist": "Portuguese reviews (used as-is) + English category map", "olist_funnel": "English codes", "retailrocket": "none (hashed)",
        "amazon_m2": "DE, JP, EN-UK, ES, FR, IT", "esci": "EN (US), ES, JP", "reviews2023": "English", "holidays": "n/a",
        "public_record": "English sources", "open_bandit": "none (anonymised)", "criteo": "none (anonymised)", "bcb": "numeric (pt field names)",
        "astronomy_shop": "English", "m5": "English codes", "instacart": "English", "uci": "English", "coat": "numeric",
        "yahoo_r3": "-", "yelpchi": "numeric features", "amazon_fraud": "numeric features", "ipinyou": "IDs, user agents (no Chinese text seen)",
        "hillstrom": "English", "nab_loghub": "English logs", "tmall": "none (numeric IDs)", "taobao": "none (numeric IDs)",
        "expedia": "none (numeric IDs)", "ga": "English field names", "statcounter": "numeric"}
BADGE = {"PASS": ("✔", "#2f9e44", "#d3f9d8"), "PARTIAL": ("◐", "#e67700", "#fff3bf"), "FAIL": ("✘", "#c92a2a", "#ffe3e3"),
         "NA": ("—", "#495057", "#e9ecef"), "WAIT": ("…", "#1971c2", "#e7f5ff"), "?": ("?", "#868e96", "#f8f9fa")}

# (data type, method, primary, [(secondary, why)], what works / what doesn't)
ROWS = [
    ("L0  External\nenvironment", [
        ("Seasonality, holidays, Black Friday", "fit + join on date", "Olist", [("Holidays", "fills gap: holiday dates")], "Olist Black Friday 6.2×; holiday rules verified"),
        ("Macro: inflation, FX, interest", "join on date", "BCB SGS", [], "3 series 2016–18, values plausible"),
        ("External shocks (2018 strike)", "replay + fit", "Olist", [("Public record", "check: strike dates")], "strike visible in Olist; dates confirmed"),
    ]),
    ("L1  Identity\n& context", [
        ("Buyers, regions", "fit", "Olist", [], "27 Brazilian states"),
        ("Sellers, regions, size", "fit", "Olist", [], "3,095 sellers with state and volume"),
        ("Products, categories, titles", "fit + bridge", "Olist", [("ESCI", "fills gap: English titles")], "Olist has no titles → ESCI (mixed-language shop, labelled)"),
        ("Devices, channels", "prior + transfer", "Statcounter", [("Google Analytics", "other part: conversion")], "Brazil device share (page views); desktop converts 3.9× mobile"),
    ]),
    ("L2  Catalog, offers\n& competition", [
        ("Prices, promotions", "fit + transfer", "Olist", [("M5", "fills gap: price changes")], "prices yes, promo flags nowhere"),
        ("Multi-seller offers", "fit + assumed", "Olist", [("Expedia", "other part: shown together")], "only 123 of 1,225 multi-seller products sold by 2 sellers in the same week"),
        ("Inventory, stock-outs", "transfer", "Retailrocket", [], "real availability flag over time (417k items)"),
        ("Search queries, relevance", "bridge", "ESCI", [("Reviews 2023", "fills gap: category map")], "130k queries with human labels"),
    ]),
    ("L3  Seller\nhealth", [
        ("Handling time, reliability", "fit", "Olist", [], "approval → carrier timestamps"),
        ("Seller entry, ramp-up", "exact join", "Olist Funnel", [("Olist", "other part: first sale")], "won date → first sale (380 join)"),
        ("Seller exit", "fit", "Olist", [], "no sale after May 2018: small sellers 92%, large 11% (censored)"),
        ("Fees, payouts", "assumed (A-10)", "Assumed", [], "no public fee/payout data found; range from general sources"),
    ]),
    ("L4  Demand\n& discovery", [
        ("Traffic volume, calendar", "anchor (levels)", "Olist", [], "orders + hourly/weekly shape; visit level via funnel anchor"),
        ("Session length, revisits", "transfer", "Taobao", [("Retailrocket", "check: visitor-days")], "second-level gaps; median 76 s"),
        ("Funnel view → cart → buy, timing", "transfer", "Retailrocket", [("Taobao", "other part: timing")], "step rates + second-level timing"),
        ("Exposure, position, clicks", "transfer", "Expedia", [], "randomised order: position 10 gets 33% of position-1 clicks"),
        ("Recommendation policy, propensities", "transfer", "Open Bandit", [], "uniform + BTS propensities logged"),
        ("Repeat purchases", "fit + transfer", "Olist", [("Instacart", "other part: intervals")], "3.1% repeat; 7-day median gap"),
        ("Ads, sponsored lift", "transfer", "Criteo", [("Hillstrom", "check: 2nd randomised")], "randomised lift + non-compliance"),
    ]),
    ("L5  Transactions\n& fulfilment", [
        ("Orders, items, payments", "replay + fit", "Olist", [], "clean joins; payments reconcile 99.75%"),
        ("Delivery promise, delays", "fit", "Olist", [], "on time 4.29★ vs late 1.71★"),
        ("Cancellations, returns", "fit + transfer", "Olist", [("UCI", "fills gap: returns")], "Olist has no returns → UCI 'C' invoices"),
    ]),
    ("L6  Trust\n& reviews", [
        ("Review score given delivery", "fit", "Olist", [], "strong delay → score link"),
        ("Review text", "pool", "Olist", [], "pt-BR, pooled by stars × delivery outcome (cause-consistent)"),
        ("Review timing (survey sent, answered)", "fit", "Olist", [], "survey at delivery/estimate; customer answers after median 40 h"),
        ("Who leaves reviews (MNAR)", "fit + transfer", "Olist", [("Coat", "fills gap: selection")], "Olist ~99% reviewed → Coat gives the shape"),
        ("Fake reviews, fraud rings", "transfer", "YelpChi", [("Amazon fraud", "other part: user rings")], "no timestamps in either"),
    ]),
    ("L7  Platform\n& infrastructure", [
        ("Service telemetry, faults", "live + shape", "Astronomy Shop", [("NAB / LogHub", "fallback: shapes")], "shop on hold (A2) → NAB shapes meanwhile"),
        ("Experiments, assignments", "transfer", "Open Bandit", [("Criteo", "check: 2nd randomised")], "randomised logs available"),
    ]),
    ("L9  Observation", [
        ("Logging gaps, delays, late data", "assumed (A-11) + evidence", "Assumed", [("Retailrocket", "evidence: missing views")], "assumption, with real evidence"),
    ]),
]


def norm(r):
    return r.replace("→", "->").strip().lower()


def load(key):
    p = DOCS / "datasets" / f"{key}.json"
    return json.loads(p.read_text()) if p.exists() else {}


def status(label, row):
    key = DS[label][0]
    if key is None:
        return None, ""
    s = load(key)
    if s.get("fetch_status") != "ok":
        reason = s.get("reason", "")
        return ("WAIT" if "not (fully) downloaded" in reason else "NA"), reason
    for c in s.get("claims", []):
        if norm(c["row"]) == norm(row):
            return c["status"], c["evidence"]
    return "?", "not audited for this row"


def pill(label, x, y, w, row):
    st, _ = status(label, row)
    colour = DS[label][1]
    red = label == "Assumed"
    box(label, x, y, w - (30 if st else 0), colour, 13, stroke="#e03131" if red else "#1e1e1e",
        style="dashed" if st in ("NA", "WAIT") else "solid", minh=32)
    if st:
        g, fg, bg = BADGE[st]
        bb = box(g, x + w - 28, y, 28, bg, 15, stroke=fg, minh=32)
        bb["strokeWidth"] = 2
    return st


def verdict(pst, sst):
    if pst is None:
        return ("ASSUMED", "#495057", "#f1f3f5")
    if pst == "PASS":
        return ("USABLE", "#2f9e44", "#d3f9d8")
    if pst == "PARTIAL" and "PASS" in sst:
        return ("USABLE*", "#1971c2", "#d0ebff")
    if pst == "PARTIAL":
        return ("CAVEATS", "#e67700", "#fff3bf")
    return ("GAP", "#c92a2a", "#ffe3e3")


text("Data stitching with the 19 datasets + 2 reference files we have: one primary + at most one justified secondary", 0, -130, 2600, 28)
text("Rule: a dataset stays only where it works (✔) or works partly on something real (◐). Nothing failing, missing or downloading remains. "
     "Verdict: USABLE = primary passes; USABLE* = primary partial, secondary passes; CAVEATS = only partial evidence; ASSUMED = no data.",
     0, -88, 2600, 15, color="#495057")

X_L, X_T, X_P, X_S, X_V, X_N = 0, 200, 545, 790, 1400, 1580
y = -30
for x, h in ((X_L, "Layer"), (X_T, "Data type  (method)"), (X_P, "PRIMARY"), (X_S, "SECONDARY (max 1) and why"), (X_V, "Verdict"), (X_N, "What works / what doesn't")):
    text(h, x + 6, y, 400, 17)
y = 10
RH = 46
counts = {}
unknown = []
for layer, items in ROWS:
    top = y
    for i, (dtype, method, prim, secs, note) in enumerate(items):
        if i % 2 == 0:
            base("rectangle", X_T - 6, y - 4, X_N + 540 - X_T + 6, RH, bg="#f8f9fa", stroke="#f8f9fa", sw=1)
        text(dtype, X_T + 6, y + 2, 330, 15)
        text(method, X_T + 6, y + 22, 330, 12, color="#868e96")
        pst = pill(prim, X_P, y, 235, dtype)
        sst = []
        for j, (sec, why) in enumerate(secs[:1]):
            sst.append(pill(sec, X_S, y, 200, dtype))
            text(why, X_S + 208, y + 8, 380, 13, color="#1971c2")
        unknown += [f"{lab} @ {dtype}" for lab, st in zip([prim] + [x[0] for x in secs], [pst] + sst) if st == "?"]
        if not secs:
            text("—", X_S + 10, y + 8, 40, 15, color="#adb5bd")
        v, fg, bg = verdict(pst, sst)
        counts[v] = counts.get(v, 0) + 1
        vb = box(v, X_V, y, 165, bg, 14, stroke=fg, minh=32)
        vb["strokeWidth"] = 2
        text(note, X_N + 6, y + 8, 540, 13, color="#343a40")
        y += RH
    base("rectangle", X_L, top - 4, 185, y - top, bg="#e7f5ff", stroke="#74c0fc", sw=1)
    text(layer.replace("  ", "\n"), X_L + 8, top + (y - top) / 2 - 20, 169, 15)
    y += 10
text("L8 ground truth and L10 lineage are produced by the system itself (simulator, provenance), not by a dataset.", X_T, y + 4, 1500, 14, color="#495057")
summary = " · ".join(f"{k} {v}" for k, v in sorted(counts.items()))
text(f"Rows: {summary}   (of {sum(counts.values())} data types)", X_T, y + 30, 1500, 18)

# legends -------------------------------------------------------------------------------------------
LX = 2160
text("Badges (audit result per dataset per row)", LX, -30, 600, 18)
ly = 10
for k, desc in (("PASS", "works: the data type is there, measured"), ("PARTIAL", "works partly, kept because the part it covers is real")):
    g, fg, bg = BADGE[k]
    b = box(g, LX, ly, 32, bg, 15, stroke=fg, minh=30)
    b["strokeWidth"] = 2
    text(desc, LX + 44, ly + 6, 520, 14)
    ly += 38
text("No ✘ fails, missing or downloading datasets remain in the design.", LX, ly, 600, 13, color="#2f9e44")
ly += 34
text("Datasets we have (19, downloaded + audited)", LX, ly, 600, 18)
ly += 32
for label, (key, colour) in DS.items():
    if key is None or label in SUPPORT or label in REFERENCE:
        continue
    st_ = load(key)
    box(label, LX, ly, 170, colour, 12, minh=26)
    text(f"{st_.get('bytes', 0) / 1e9:.2f} GB · lang: {LANG.get(key, '?')}", LX + 180, ly + 5, 600, 11, color="#495057")
    ly += 31
ly += 10
text("Reference files (2)", LX, ly, 600, 16)
ly += 28
for label, why in REFERENCE.items():
    box(label, LX, ly, 170, DS[label][1], 12, minh=26)
    text(why, LX + 180, ly + 5, 600, 11, color="#495057")
    ly += 31
ly += 10
text("Support (not counted)", LX, ly, 600, 16)
ly += 28
for label, why in SUPPORT.items():
    box(label, LX, ly, 170, DS[label][1], 12, minh=26)
    text(why, LX + 180, ly + 5, 600, 11, color="#495057")
    ly += 31
ly += 10

METHODS = [
    ("replay", "real recorded events fed through unchanged"), ("live", "produced by running real software; recorded"),
    ("exact join", "linked through a real shared key"), ("join on date", "exact join to a series by date (+ region)"),
    ("fit", "parameters estimated on the primary dataset"), ("anchor", "base rates solved to match Olist volume/calendar"),
    ("transfer", "relative shape learned elsewhere; levels from Olist"), ("bridge", "synthetic entity dressed with a real record (not identity)"),
    ("pool", "real values sampled by condition"), ("shape", "time pattern only; size and cause assumed"),
    ("assumed", "no dataset; explicit assumption with a range"),
]
my = ly + 50
text("Methods (strongest evidence first)", LX, my, 600, 18)
my += 32
for name, desc in METHODS:
    box(name, LX, my, 120, "#f1f3f5" if name != "assumed" else "#ffffff", 12, minh=26, stroke="#e03131" if name == "assumed" else "#495057")
    text(desc, LX + 130, my + 5, 560, 12, color="#343a40")
    my += 32

TECH = [
    ("Hugging Face (HF)", "Public dataset hub. ESCI mirror, Open Bandit, Criteo and Reviews 2023 are hosted there."),
    ("Kaggle / Tianchi", "Free-account platforms (Kaggle: Olist, Retailrocket, M5, Instacart, Expedia, Google Analytics; Tianchi: Taobao)."),
    ("Statcounter", "Web-analytics service publishing device share by country from page views on sites using its tracker; free CSV export."),
    ("BCB SGS API", "Brazil's central bank time-series API: inflation (IPCA), interest rate (Selic), USD/BRL."),
    ("scripts/datasets.py", "Retrieves each dataset into ~/data/mkt/raw, fingerprints files (SHA-256) and audits the claims shown here."),
    ("scripts/stitch.py", "The stitching script (not run yet): one step per data type, writes outputs + a provenance manifest."),
    ("Transport card", "Per borrowed mechanism: source vs target population, overlap, assumptions, uncertainty, held-out check, status (unsupported = labelled)."),
    ("trained_through", "Latest data date a fitted mechanism used; replay refuses mechanisms fitted on data later than the simulation clock."),
    ("Re-anchoring solve", "Small fit that sets base rates so stitched pieces together reproduce Olist's real volume and calendar."),
    ("ASIN / parent ASIN", "Amazon product IDs; variants roll up to a parent. Matching on the wrong level undercounts (~10% measured)."),
    ("Propensity", "Known probability that an action was chosen; needed to estimate causal effects from logged data."),
    ("MNAR", "Missing not at random: whether data is missing depends on its value (e.g. unhappy customers review more)."),
    ("Uplift / non-compliance", "Effect of a treatment (an ad); non-compliance = assigned but not exposed (Criteo records both)."),
]
tech_legend(TECH, 0, max(y + 90, my + 40), cols=2, colw=1300)

out = DOCS / "stitching.excalidraw"
print(out, save(out), "elements", counts)
if unknown:
    print("not audited for that row:", unknown)
