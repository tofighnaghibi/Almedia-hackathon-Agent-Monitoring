"""Build docs/brain-architecture.html from its template, the code of the brain and the measured results.

    python docs/build_brain_architecture.py [--fragment PATH]

What the page says is read from the code when it is built: the graph figure is drawn from
MetricGraph.load() (a metric that enters the outcome tree without a position here stops the build), the
thresholds come from the constants of the brain's modules, the module table from the files in brain/ and
tests/, the results from docs/results.json. The numbers that are typed are marked below, each with the
place it comes from. --fragment also writes the page without the document wrapper, for publishing it
elsewhere. Open the result in a browser.
"""
from __future__ import annotations

import argparse
import ast
import html
import importlib
import json
import math
import pathlib
import re
import sys
from collections import Counter
from datetime import datetime

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from brain import clock, extension, incidents, investigate, localize, signature, validate  # noqa: E402
from brain.attribution import leaves  # noqa: E402
from brain.data import READABLE_TABLES  # noqa: E402
from brain.graph import MetricGraph  # noqa: E402
from brain.impact import tree_metrics  # noqa: E402
from brain.metric_spec import DIMENSIONS, MetricSpec  # noqa: E402
from brain.monitor import Config, watch_list  # noqa: E402
from scripts.summarize_evaluation import tally  # noqa: E402
from sim import world  # noqa: E402

# Typed, because they need a database and the build has none. Refresh by hand (about 5 s):
#     python -m sim.run --backfill 28d --db <scratch>/market.db --force
#     python -m brain.graph --validate --days 28 --db <scratch>/market.db
# The second command prints "Validation: 52 checks on data passed, ...; 28 more hold by construction".
# Last checked on 21 Nov 2026 with the default seed (42).
DATA_CHECKS = 52
DEFINITION_CHECKS = 28
VALIDATION_DAYS = 28   # the --days of the command above

g = MetricGraph.load()
cfg = Config()
esc = html.escape

# ------------------------------------------------------------------ facts read from the code
WATCHED = watch_list(g)
OUTCOME = g.top_outcomes()[0]
# The values of each dimension as the simulator writes them; the brain reads them from the data.
SIZES = dict(zip(DIMENSIONS, (len(world.COUNTRIES), len(world.PLATFORMS), len(world.CATEGORIES), len(world.CHANNELS))))
N_SERIES = sum(math.prod(SIZES[d] for d in cut) for cut in cfg.cuts)       # series of one rate over all cuts
N_CELLS = math.prod(SIZES.values())                                       # the finest cells
N_PLACES = math.prod(n + 1 for n in SIZES.values()) - 1                   # every place the dimensions can name
CHECKS = re.findall(r"^  ([a-z]+) {2,}\S", extension.__doc__, flags=re.M)  # the steps of brain/extension.py, in its order
COLUMN_METRICS = [m for m, s in g.specs.items() if s.column]
N_CANDIDATES = len(list(extension.candidates(COLUMN_METRICS)))
ARRIVAL_MINUTES = int(clock.ARRIVAL_MARGIN / pd.Timedelta(minutes=1))
DEFAULT_TOLERANCE = MetricSpec(name="x").tolerance

README = (ROOT / "README.md").read_text(encoding="utf-8")
# README 3.8, measured during the design review: the deviation of three series in units of their noise.
_noise = re.search(r"total orders [−-]([\d.]+), orders in the cell [−-]([\d.]+), payment success rate in the cell [−-]([\d.]+)", README)
assert _noise, "README 3.8 no longer has the measurement the tick figure quotes"
NOISE_TOTAL, NOISE_CELL, NOISE_RATE = _noise.groups()

WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
         11: "eleven", 12: "twelve", 20: "twenty"}


def word(n: int) -> str:
    return WORDS.get(n, str(n))


def num(x: float) -> str:
    return f"{x:g}"


NBSP = chr(0xA0)


def pct(x: float) -> str:
    """A share as the README writes it, "10 %", with a space that does not break the line."""
    return f"{x:.0%}".replace("%", NBSP + "%")


def sci(x: float) -> str:
    """A small number the way the README writes it: 1e-6, not 1e-06 or 0.000001."""
    mantissa, exponent = f"{x:e}".split("e")
    return f"{float(mantissa):g}e{int(exponent)}"


def p_below(surprise: float) -> str:
    """A surprise is -log10 of a p-value: 7 is p below 1e-7, 1.3 is p below 0.05."""
    p = 10 ** -surprise
    return f"1e-{surprise:g}" if float(surprise).is_integer() and surprise > 2 else f"{round(p, 2):g}"


# ------------------------------------------------------------------ text fitting
# Advance widths in em, a little wider than Inter's, so a line that fits here fits in the browser,
# with Inter or with the system font that stands in for it.
_NARROW = set("iljI.,:;'!|()[] ")
_MID = set("tfr-·\"/*")
_WIDE = set("mwMW—→×")


def text_px(text: str, px: float = 11.0, mono: bool = False) -> float:
    if mono:
        return len(text) * 0.61 * px
    em = sum(0.30 if c in _NARROW else 0.40 if c in _MID else 0.90 if c in _WIDE
             else 0.68 if c.isupper() or c.isdigit() else 0.58 for c in text)
    return em * px


def wrap(text: str, width: float, px: float = 11.0) -> list[str]:
    lines, cur = [], ""
    for w in text.split(" "):          # not on a no-break space
        trial = f"{cur} {w}".strip(" ")
        if cur and text_px(trial, px) > width:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    return lines + [cur] if cur else lines


# ------------------------------------------------------------------ small SVG helpers
def rich(x, y, parts, tab=None) -> str:
    """One line of text made of (text, class) pieces: a module name in mono, then what it does.
    `tab`: where the second piece starts, counted from x, so that the lines of a box form two columns."""
    spans = []
    for i, (t, c) in enumerate(parts):
        at = "" if not i else f' x="{x + tab}"' if tab else ' dx="7"'
        spans.append(f'<tspan class="{c}"{at}>{esc(t)}</tspan>')
    return f'<text x="{x}" y="{y}">' + "".join(spans) + "</text>"


def named(x, y, rows, step=15) -> str:
    """Lines of (name, what it does): the names in mono, the texts in one column right of the longest name."""
    tab = round(max(text_px(n, 11.5, mono=True) for n, _ in rows) + 9)
    return "\n".join(rich(x, y + i * step, [(n, "mono"), (t, "tm")], tab) for i, (n, t) in enumerate(rows))


def card(x, y, w, h, title, lines, cls="box", line_cls="tm", step=15):
    out = [f'<rect class="{cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="6"/>',
           f'<text class="tt" x="{x + 12}" y="{y + 21}">{esc(title)}</text>']
    for i, ln in enumerate(lines):
        c = line_cls
        if isinstance(ln, tuple):
            ln, c = ln
        out.append(f'<text class="{c}" x="{x + 12}" y="{y + 39 + i * step}">{esc(ln)}</text>')
    return "\n".join(out)


def arrow(x1, y1, x2, y2, cls="edge", marker="ar", both=False):
    start = f' marker-start="url(#{marker})"' if both else ""
    return f'<line class="{cls}" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" marker-end="url(#{marker})"{start}/>'


def elbow(points, cls="edge", marker="ar"):
    d = "M" + " L".join(f"{x},{y}" for x, y in points)
    return f'<path class="{cls}" d="{d}" marker-end="url(#{marker})"/>'


def note(x, y, text, anchor="start"):
    end = ' text-anchor="end"' if anchor == "end" else ""
    return f'<text class="lbl" x="{x}" y="{y}"{end}>{esc(text)}</text>'


def defs(prefix):
    return (f'<defs>'
            f'<marker id="{prefix}ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" class="mk"/></marker>'
            f'<marker id="{prefix}am" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" class="mk-m"/></marker>'
            f'<marker id="{prefix}ac" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" class="mk-c"/></marker>'
            f'</defs>')


def card_rows(prefix: str, rows: list[list[dict]], aria: str, width: int = 1062, margin: int = 16, gap: int = 12,
              row_gap: int = 30) -> str:
    """Rows of numbered cards, read left to right and row after row. A card has a title, where the code is
    (`where`), a text that is wrapped to the card, an optional last line (`foot`) and an optional box class."""
    s = [defs(prefix)]
    y = margin
    turn = None   # where the row above ends: the arrow into this row starts there
    for row in rows:
        w = (width - 2 * margin - (len(row) - 1) * gap) / len(row)
        laid = [(c, wrap(c["text"], w - 24)) for c in row]
        top = 55 if any(c.get("where") for c in row) else 41            # baseline of the first text line
        body = max(len(lines) for _, lines in laid)
        h = top + 14 * (body - 1) + (19 if any(c.get("foot") for c in row) else 0) + 11
        if turn is not None:
            mid = y - row_gap / 2
            s.append(elbow([(turn, y - row_gap), (turn, mid), (margin + w / 2, mid), (margin + w / 2, y - 2)],
                           marker=f"{prefix}ar"))
        for i, (c, lines) in enumerate(laid):
            x = round(margin + i * (w + gap), 1)
            s.append(f'<rect class="{c.get("cls", "box")}" x="{x}" y="{y}" width="{round(w, 1)}" height="{h}" rx="6"/>')
            s.append(f'<text class="tt" x="{x + 12}" y="{y + 21}">{esc(c["title"])}</text>')
            if c.get("where"):
                s.append(note(x + 12, y + 37, c["where"]))
            for j, ln in enumerate(lines):
                s.append(f'<text class="ts" x="{x + 12}" y="{y + top + 14 * j}">{esc(ln)}</text>')
            if c.get("foot"):
                s.append(note(x + 12, y + h - 11, c["foot"]))
            if i:
                s.append(arrow(x - gap + 1, y + h / 2, x - 2, y + h / 2, marker=f"{prefix}ar"))
        turn = round(margin + (len(row) - 1) * (w + gap) + w / 2, 1)
        y += h + row_gap
    height = y - row_gap + margin
    return f'<svg class="d" viewBox="0 0 {width} {height}" role="img" aria-label="{esc(aria)}">' + "\n".join(s) + "</svg>"


# ------------------------------------------------------------------ figure 1: from data to incident
# The modules the figure names. The page says each has tests; the build checks it.
FIGURE_MODULES = ("extension", "metric_spec", "formula", "graph", "validate", "data", "evaluator", "run", "clock", "stats",
                  "monitor", "incidents", "investigate", "localize", "attribution", "impact")


def fig_pipeline():
    counts = Counter(e.kind for e in g.edges)
    s = [defs("p-")]
    A = lambda *a, **k: arrow(*a, marker="p-ar", **k)  # noqa: E731
    # left column: what comes in, and the two modules through which every part reads the data
    s.append(card(16, 16, 180, 104, "<name>.yaml",
                  ["a new metric definition:", "a formula, a query or both,", "written into", ("incoming_metrics/", "mono")]))
    s.append(card(16, 154, 180, 124, "simulator database",
                  [(t, "mono") for t in READABLE_TABLES] + [("ground_truth: not readable", "tw")]))
    s.append(card(16, 332, 180, 98, "data.py · Store",
                  ["read-only connection", f"only the {len(READABLE_TABLES)} contract tables:", "SQLite refuses the rest",
                   "NULL is never summed as 0"]))
    s.append(card(16, 466, 180, 68, "evaluator.py", ["any metric, from sums", "a rate is a ratio of sums"]))
    s.append(A(106, 278, 106, 329))
    s.append(A(106, 430, 106, 463))
    # row 1: a new metric at run time
    s.append(card(226, 16, 602, 104, "extension.py · extension agent", [
        f"polled by the live loop; takes a file once it reads the same on {word(extension.SETTLE_LOOKS)} looks in a row",
        f"{len(CHECKS)} checks, from reading the file to the watch list; the query runs with a time and a row limit",
        "validated or tentative: the metric joins the graph · rejected: it stays out",
        "no language model"]))
    s.append(card(856, 16, 178, 46, "<name>.result.json", ["status and every check"]))
    s.append(card(856, 74, 178, 46, "graph export", ["rewritten for the console"]))
    s.append(A(196, 68, 223, 68))
    s.append(A(828, 39, 853, 39))
    s.append(A(828, 97, 853, 97))
    s.append(A(705, 120, 705, 151))
    s.append(note(713, 141, "adopts a checked metric (MetricGraph.adopt)"))
    # row 2: the model
    s.append(card(226, 154, 150, 74, "metrics/*.yaml", [f"{len(g.specs)} metric definitions", "formulas and causes"]))
    s.append(card(404, 154, 150, 74, "metric_spec.py", ["specs checked on load", "formula.py: parser"]))
    s.append(card(582, 154, 246, 74, "graph.py · MetricGraph",
                  [f"{len(g.specs)} metrics, {len(g.edges)} typed edges", f"declared outcome: {OUTCOME}"], cls="box-a"))
    s.append(card(856, 154, 178, 74, "validate.py", [f"{DATA_CHECKS} checks on data", f"{DEFINITION_CHECKS} hold by definition"]))
    s.append(A(376, 191, 401, 191))
    s.append(A(554, 191, 579, 191))
    s.append(A(828, 191, 853, 191))
    s.append(A(705, 228, 705, 259))
    s.append(note(697, 249, "watch list · Monitor.rewatch after a new metric", anchor="end"))
    s.append(note(713, 249, "cause links, system tags, formulas"))
    # the loop: one frame for what run.py drives, the parts inside it
    s.append('<rect class="box" x="226" y="262" width="808" height="284" rx="6"/>')
    s.append('<text class="tt" x="238" y="283">run.py · the loop</text>')
    s.append('<text class="tm" x="238" y="301">python -m brain.run. Live: on every poll it looks into incoming_metrics/ '
             '(Extension.poll) and ticks when a new data hour is there.</text>')
    s.append('<text class="tm" x="238" y="316">--replay walks a finished database hour by hour and reads no row from the '
             'future. One tick: Monitor.tick(), then Book.update(tick).</text>')
    s.append(card(238, 332, 262, 98, "monitor", []))
    s.append(named(250, 371, [("clock.py", "clock, arrival gate"), ("stats.py", "how surprising a count is"),
                              ("monitor.py", "baselines, tests, alerts")]))
    s.append(f'<text class="tm" x="250" y="416">{len(WATCHED)} rates in {len(cfg.cuts)} cuts, judged on every tick</text>')
    s.append(card(528, 332, 228, 98, "incidents.py", [
        "groups alerts into incidents;", "one file each, updated in place:", "open → ongoing → resolved;",
        "explained and pending records"]))
    s.append(card(784, 332, 238, 98, "incident files", []))
    s.append(named(796, 371, [("inc-NNNN.json", "an incident"), ("explained-*.json", "a calendar event"),
                              ("pending-*.json", "hours held")]))
    s.append('<text class="tm" x="796" y="416">read by the console and the scorer</text>')
    s.append(A(500, 381, 525, 381))
    s.append(A(756, 381, 781, 381))
    s.append('<rect class="box" x="238" y="466" width="784" height="68" rx="6"/>')
    s.append('<text class="tt" x="250" y="487">investigator</text>')
    s.append(named(250, 505, [("investigate.py", "cause or symptom, hypotheses, trail, report"),
                              ("localize.py", "every place scored for one common shift")]))
    s.append(named(650, 505, [("attribution.py", "the cost, split over the factors"),
                              ("impact.py", "the outcome recomputed through the formulas")]))
    s.append(A(642, 433, 642, 463, both=True))
    s.append(note(650, 452, "one report for every incident with a live alert"))
    s.append(A(196, 381, 223, 381))
    s.append(A(196, 500, 223, 500))
    aria = ("Metric definitions become a typed graph. The store and the evaluator turn the simulator's tables into metric "
            "values. In the loop of run.py the monitor judges the watched rates and reports alerts, incidents.py groups them "
            "into incident files, and the investigator writes the report of every incident. The extension agent takes a new "
            "definition from incoming_metrics, answers in a result file, adds a checked metric to the graph, writes the "
            "graph export again and extends the monitor's watch list.")
    return f'<svg class="d" viewBox="0 0 1050 562" role="img" aria-label="{esc(aria)}">' + "\n".join(s) + "</svg>", counts


# ------------------------------------------------------------------ figure 2: the metric graph
W, H = 158, 30
POS = {
    "contribution": (446, 16),
    "ad_spend": (120, 86), "marketplace_fees": (446, 86), "discounts": (780, 86),
    "ad_cpc": (16, 156), "ad_clicks": (190, 156), "take_rate": (400, 156), "gmv": (613, 156), "discount_rate": (876, 156),
    "ad_ctr": (60, 226), "ad_impressions": (234, 226), "orders": (530, 226), "aov": (704, 226),
    "ad_load": (150, 296), "sessions": (382, 296), "conversion_rate": (620, 296),
    "views_per_session": (16, 366), "add_to_cart_rate": (188, 366), "checkout_rate": (360, 366),
    "payment_attempt_rate": (532, 366), "payment_success_rate": (704, 366), "order_rate": (876, 366),
}
CAUSE_Y = 456
CAUSES = {"search_ctr": 16, "feed_ctr": 188, "stockouts": 396}
OPS = {"term+": "+", "term-": "−", "factor": "×", "numerator": "÷", "denominator": "÷"}
SIGN = {1: "+", -1: "−", 0: "?"}


def node(x, y, label, cls="n", w=W, text_cls="nt"):
    return (f'<rect class="{cls}" x="{x}" y="{y}" width="{w}" height="{H}" rx="5"/>'
            f'<text class="{text_cls}" x="{x + w / 2}" y="{y + 19.5}" text-anchor="middle">{esc(label)}</text>')


def fig_graph():
    s = [defs("g-")]
    formula_edges = [e for e in g.edges if e.kind == "formula"]
    missing = {m for e in formula_edges for m in (e.src, e.dst)} - set(POS)
    assert not missing, f"no position for {missing}"
    parents = {}
    for e in formula_edges:
        parents.setdefault(e.dst, []).append(e.src)
    shared = {m for m, ps in parents.items() if len(ps) > 1}

    # formula edges first, so boxes are drawn over their ends
    for e in formula_edges:
        px, py = POS[e.src]
        cx, cy = POS[e.dst]
        ps = sorted(parents[e.dst], key=lambda p: POS[p][0])
        off = (ps.index(e.src) - (len(ps) - 1) / 2) * 48
        x1, y1, x2, y2 = px + W / 2, py + H, cx + W / 2 + off, cy
        s.append(f'<path class="e-f" d="M{x1},{y1} C{x1},{y1 + 22} {x2},{y2 - 22} {x2},{y2}"/>')
        s.append(f'<text class="op" x="{x2 + 5}" y="{y2 - 5}">{OPS.get(e.attrs.get("role", ""), "")}</text>')

    for m, (x, y) in POS.items():
        s.append(node(x, y, m, "n-shared" if m in shared else ("n-root" if g.specs[m].outcome else "n")))

    # possible causes
    for m, x in CAUSES.items():
        s.append(node(x, CAUSE_Y, m, "n-cause"))
    s.append(node(560, CAUSE_Y, "discount_rate", "n-ref"))
    s.append(f'<text class="tm" x="{560 + W / 2}" y="{CAUSE_Y + 44}" text-anchor="middle">same node as in the tree</text>')

    def bottom(metric, dx):
        x, y = POS[metric]
        return x + W / 2 + dx, y + H + 3

    def cause_edge(x1, y1, target, dx, sign):
        x2, y2 = bottom(target, dx)
        s.append(f'<line class="e-m" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" marker-end="url(#g-am)"/>')
        lx, ly = x1 + (x2 - x1) * 0.3, y1 + (y2 - y1) * 0.3
        s.append(f'<text class="sg" x="{lx + 6}" y="{ly + 4}">{SIGN[sign]}</text>')

    signs = {(e.src, e.dst): e.attrs.get("sign", 0) for e in g.edges if e.kind == "may_affect"}
    cause_edge(80, CAUSE_Y, "views_per_session", -15, signs[("search_ctr", "views_per_session")])
    cause_edge(215, CAUSE_Y, "views_per_session", 30, signs[("feed_ctr", "views_per_session")])
    cause_edge(290, CAUSE_Y, "add_to_cart_rate", -25, signs[("feed_ctr", "add_to_cart_rate")])
    cause_edge(460, CAUSE_Y, "add_to_cart_rate", 15, signs[("stockouts", "add_to_cart_rate")])
    cause_edge(640, CAUSE_Y, "add_to_cart_rate", 50, signs[("discount_rate", "add_to_cart_rate")])
    s.append(f'<line class="e-m" x1="396" y1="{CAUSE_Y + 15}" x2="349" y2="{CAUSE_Y + 15}" marker-end="url(#g-am)"/>')
    s.append(f'<text class="sg" x="368" y="{CAUSE_Y + 9}">{SIGN[signs[("stockouts", "feed_ctr")]]}</text>')
    assert len(signs) == 6, signs

    # context examples
    s.append(node(728, CAUSE_Y, "release: payments-sdk", "n-ctx", w=160, text_cls="nt nt-s"))
    s.append(node(898, CAUSE_Y, "freshness: payments", "n-ctx", w=140, text_cls="nt nt-s"))
    x2, y2 = bottom("payment_success_rate", 10)
    s.append(f'<line class="e-c" x1="808" y1="{CAUSE_Y}" x2="{x2}" y2="{y2}" marker-end="url(#g-ac)"/>')
    x2, y2 = bottom("payment_success_rate", 55)
    s.append(f'<line class="e-c" x1="968" y1="{CAUSE_Y}" x2="{x2}" y2="{y2}" marker-end="url(#g-ac)"/>')
    s.append(node(406, 226, "calendar", "n-ctx", w=110))
    sx, sy = POS["sessions"]
    s.append(f'<line class="e-c" x1="461" y1="{226 + H}" x2="{sx + W / 2}" y2="{sy - 3}" marker-end="url(#g-ac)"/>')

    # legend
    y = 536
    s.append(f'<line class="e-f" x1="16" y1="{y}" x2="52" y2="{y}"/><text class="tm" x="60" y="{y + 4}">formula: exact identity (+ − × show the role)</text>')
    s.append(f'<rect class="n-shared" x="330" y="{y - 8}" width="26" height="16" rx="4"/><text class="tm" x="364" y="{y + 4}">feeds two parents: the graph is a DAG</text>')
    s.append(f'<line class="e-m" x1="600" y1="{y}" x2="636" y2="{y}" marker-end="url(#g-am)"/><text class="tm" x="646" y="{y + 4}">possible cause, with sign</text>')
    s.append(f'<line class="e-c" x1="820" y1="{y}" x2="856" y2="{y}" marker-end="url(#g-ac)"/><text class="tm" x="866" y="{y + 4}">context (3 of {sum(1 for e in g.edges if e.kind == "context")} shown)</text>')
    label = ("The metric graph as loaded from the YAML files: contribution splits into marketplace fees plus ad spend minus "
             "discounts and further down to the six step rates of the funnel; gmv and sessions feed two parents; possible "
             "causes point at step rates; releases, data freshness and the calendar are context.")
    return f'<svg class="d" viewBox="0 0 1050 556" role="img" aria-label="{esc(label)}">' + "\n".join(s) + "</svg>"


# ------------------------------------------------------------------ figure 3: one tick of the loop
# The words below describe brain/monitor.py (Config and its docstrings), brain/incidents.py and
# brain/investigate.py; every number in them is read from those modules.
TRAIL = ("watch", "compare", "drill", "follow", "decompose", "check", "compare", "stop")


def cuts_text() -> str:
    """The cuts of Config, in words: the total, the single dimensions, the pairs."""
    singles = [c for c in cfg.cuts if len(c) == 1]
    pairs = [" × ".join(c) for c in cfg.cuts if len(c) > 1]
    assert () in cfg.cuts and len(singles) == len(DIMENSIONS) and len(singles) + len(pairs) + 1 == len(cfg.cuts)
    return f"the total, each of the {word(len(singles))} dimensions, " + " and ".join(pairs)


def fig_tick():
    source = (ROOT / "brain" / "investigate.py").read_text(encoding="utf-8")
    assert all(f'"action": "{a}"' in source for a in TRAIL), "the trail's steps changed in brain/investigate.py"
    counted = [m for m in tree_metrics(g, OUTCOME) if m != OUTCOME and g.specs[m].unit == "count"
               and g.specs[m].direction == "up" and g.specs[m].investigative_formula is not None]
    assert "orders" in counted and investigate.RELEASE_BEFORE_HOURS == investigate.RELEASE_AFTER_HOURS
    widest = max(cfg.windows)
    monitor_row = [
        {"title": "1 · clock and gate", "where": "clock.py", "text":
            f"The clock is the newest data hour plus 1 h. A source has arrived for an hour when its log row is dated no "
            f"later than the clock plus {ARRIVAL_MINUTES} min. An hour of a metric is judged when all its sources are in. "
            f"Until then it is held: not healthy, not an alert."},
        {"title": "2 · what is watched", "where": "monitor.py · watch_list", "text":
            f"Every atomic rate of the graph, {len(WATCHED)} today, in {len(cfg.cuts)} cuts: {cuts_text()}. That is "
            f"{N_SERIES} series per rate. Outcomes are not judged: a fault in one cell is lost in their noise."},
        {"title": "3 · expected rate", "where": "monitor.py", "text":
            f"Pooled numerator over pooled denominator of the series' own history, up to {cfg.lookback_hours // 24} days. "
            f"Left out: the hours being judged, hours already flagged, calendar events, held hours and the hours of an "
            f"open incident. A fault that lasts does not become normal."},
        {"title": "4 · test", "where": "stats.py", "text":
            f"Chosen per metric from its history. Counts with a binomial spread: the exact two-sided tail, p below "
            f"{p_below(cfg.min_surprise)}. Other ratios: the residual beyond {num(cfg.min_z)} historical spreads and "
            f"{num(cfg.beyond_history)} times the largest seen. An hour alone and summed with the {word(widest - 1)} "
            f"before; the rate moved by {pct(cfg.min_effect)} or more."},
        {"title": "5 · after a release", "where": "monitor.py", "text":
            f"For {cfg.release_watch_hours} h after a release the metrics linked to its system are tested in its scope, on "
            f"the sum since it went out. It is one question, so p below {p_below(cfg.release_min_surprise)} is enough. "
            f"Exact tests only. This finds a fault in a small cell at night."},
    ]
    incident_row = [
        {"title": "6 · incidents", "where": "incidents.py", "text":
            f"Per metric the strongest series of a place leads. Alerts join one incident when one place lies inside the "
            f"other and the graph relates the metrics. An open incident keeps its file and its id. It is resolved at the "
            f"earliest {incidents.RESOLVE_AFTER_HOURS} h after the last bad hour, once the data shows health."},
        {"title": "7 · where", "where": "localize.py", "text":
            f"Every place the dimensions can name is scored on the {N_CELLS} finest cells: up to {N_PLACES} places and the "
            f"whole. The score is the likelihood gain of one common shift in the place, minus the price of the search. "
            f"The monitor's place is confirmed, narrowed, widened or kept."},
        {"title": "8 · what it cost", "where": "attribution.py", "text":
            f"The outcome as observed against the outcome with the moved rates back at their baselines, through every "
            f"path of the formulas: LMDI, with Shapley as the fallback. In {g.specs[OUTCOME].unit.upper()} and in "
            f"{', '.join(counted[:-1]) + ' and ' + counted[-1] if len(counted) > 1 else counted[0]}. "
            f"The baselines are frozen at the first report of the cost."},
        {"title": "9 · why", "where": "investigate.py", "text":
            f"Every release from {investigate.RELEASE_LOOKBACK_HOURS} h before the onset is scored: is its system linked "
            f"to what moved, does its scope fit the place, did the change start with it (within "
            f"{investigate.RELEASE_BEFORE_HOURS} h, or earlier when the hours in between already lean the same way)? "
            f"All three: supported. The moved metric itself, a calendar event, late "
            f"data and a mix shift are tested too."},
        {"title": "10 · trail and report", "where": "investigate.py", "text":
            f"Each question and its answer is a step of the trail: {', '.join(TRAIL)}. Nothing stops early. The report is "
            f"template text. What stays open is listed under \"uncertain\"."},
    ]
    aria = ("One tick of the loop in ten steps. The monitor: the clock and the arrival gate, what is watched, the expected "
            "rate of every series, the test, the watch after a release. Then the incidents: alerts are grouped into "
            "incidents, the place is found, the cost is computed, every explanation is tested, and the trail and the "
            "report are written.")
    return card_rows("t-", [monitor_row, incident_row], aria)


# ------------------------------------------------------------------ figure 4: a new metric
def fig_new_metric():
    ext = extension
    assert ext.SQL_TIMEOUT == int(ext.SQL_TIMEOUT)
    # the shapes the search tries, one per entry of extension._OPS; a shape is kept on one line
    shapes = ", ".join(s.replace(" ", NBSP) for s in ("a", "a / b", "a * b", "a + b", "a - b"))
    assert len(ext._OPS) == 5, "the formula search of brain/extension.py tries other shapes now"
    texts = {
        "read": (f"A mapping with a new lower_snake_case name and a formula, a query or both. The formula uses metrics the "
                 f"graph has. A tolerance, when given, is above 0 and at most {sci(ext.MAX_TOLERANCE)}.", "if not: rejected"),
        "sql": (f"The query runs inside the contract tables, for at most {ext.SQL_TIMEOUT:g} s and {ext.SQL_MAX_ROWS:,} "
                f"rows. It returns ts, value and the dimensions it groups by: one row per hour and segment.",
                "if not: rejected"),
        "formula": (f"The given one. With a query only: each of {N_CANDIDATES:,} candidates ({shapes} over the "
                    f"{len(COLUMN_METRICS)} column metrics) is computed on the same rows. The one that reproduces every "
                    f"value is taken.", "none fits: rejected · several fit: tentative"),
        "definition": (f"The query against the formula at the query's grain, row by row, within the tolerance "
                       f"({sci(DEFAULT_TOLERANCE)} unless the file sets its own).", "a row disagrees: rejected"),
        "rows": ("The two row sets, in both directions. Rows the query has and the facts lack: rejected. Rows the facts "
                 "have and the query lacks or leaves empty: a filtered metric.", "a filtered metric: tentative"),
        "split": (f"Is the query a function of sums? It runs again on a private in-memory copy of the last "
                  f"{ext.SPLIT_HOURS} hours, as they are and with every fact row split in two. An average or a count of "
                  f"rows changes.", "the values change: tentative"),
        "alias": (f"Its numeric signature, the value of the definition on {signature.N_POINTS} random points, against "
                  f"every metric of the graph. The same definition under another name is reported and not added.",
                  "an alias: rejected"),
        "rollup": ("What the breakdown links promise: the values in the segments, added up or weighted by the denominator, "
                   "give the formula on the totals, for every dimension.", "it does not hold: tentative"),
        "coverage": (f"validate.py runs on the whole graph with the metric in it, on the last {ext.VALIDATE_DAYS} days: "
                     f"values in at least {pct(validate.MIN_COVERAGE)} of the hours, and inside the bounds when the file "
                     f"declares them.", "a failed check: rejected"),
        "placement": ("Identities that hold for any data and link the metric to two existing ones, found by signature. One "
                      "fitting whole outside the outcome tree: applied. Inside the outcome tree: proposed, left to a "
                      "person.", "two places fit: tentative"),
        "context": ("The metric takes the context of the metrics its formula is built from (and of the watched rates it "
                    "equals the product of): their release systems, so it gets release candidates and, through them, the "
                    "promo calendar, and their sources, so late data holds it too.", "nothing to inherit: said so"),
        "monitoring": ("A ratio of two column metrics is an atomic rate, the shape the monitor watches. Monitor.rewatch adds "
                       "it to the watch list, and it is judged from the next tick.", "another shape: in the graph, not watched"),
    }
    assert list(texts) == CHECKS, f"the steps of brain/extension.py changed: {CHECKS}"
    cards = [{"title": f"{i} · {name}", "text": texts[name][0], "foot": texts[name][1]} for i, name in enumerate(CHECKS, 1)]
    cards.append({"title": "<name>.result.json", "cls": "box-a", "text":
                  "validated: in the graph, every check passed. tentative: in the graph, with a caveat the message names. "
                  "rejected: not in the graph, and the message says why.", "foot": "every check is in the file, with its numbers"})
    per_row = 4
    rows = [cards[i:i + per_row] for i in range(0, len(cards), per_row)]
    if len(rows) > 1 and len(rows[-1]) < per_row:   # a short last row joins the row above it (rows of 4 and one of 4 + n)
        rows[-2:] = [rows[-2] + rows[-1]]
    aria = ("The checks a new metric definition goes through, in order: " + ", ".join(CHECKS) + ". The answer file gives "
            "the status validated, tentative or rejected, and every check with its numbers.")
    return card_rows("x-", rows, aria)


# ------------------------------------------------------------------ tests of the brain
def brain_tests():
    """(test files that import from brain, test functions in them, module -> the test files that import it)."""
    files, functions, by_module = [], 0, {}
    for p in sorted((ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        used = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module and n.module.split(".")[0] == "brain":
                used |= {a.name for a in n.names} if n.module == "brain" else {n.module.split(".")[1]}
            elif isinstance(n, ast.Import):
                used |= {a.name.split(".")[1] for a in n.names if a.name.startswith("brain.")}
        if not used:
            continue
        files.append(p.stem)
        functions += sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")
                         for n in ast.walk(tree))
        for m in used:
            by_module.setdefault(m, []).append(p.stem)
    return files, functions, by_module


def count_tests(stem: str) -> int:
    tree = ast.parse((ROOT / "tests" / f"{stem}.py").read_text(encoding="utf-8"))
    return sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test") for n in ast.walk(tree))


# ------------------------------------------------------------------ module table
# (module, job, main entry points). The entry points are looked up in the module when the page is built:
# a name that does not exist stops the build. A job says what the module's docstring and code say.
GROUPS = [
    ("Foundation: the model and the data", [
        ("formula.py", "Safe parser and evaluator for metric formulas; knows whether a formula is a sum, a product or a ratio",
         ["parse", "Formula.kind", "Formula.roles", "Formula.evaluate"]),
        ("metric_spec.py", "Metric definitions from YAML, checked field by field",
         ["MetricSpec", "CauseLink", "load_specs", "spec_from_dict"]),
        ("graph.py", "The typed graph: five edge kinds, declared outcome, tree view, JSON export for the console; takes "
                     "over a changed graph in place, so a new metric needs no restart",
         ["MetricGraph.load", "MetricGraph.top_outcomes", "MetricGraph.sources", "MetricGraph.adopt", "MetricGraph.to_dict"]),
        ("signature.py", "Numeric signature of a definition: tells an identity that holds by construction from one the "
                         "data must confirm; also how an alias and a place for a new metric are found",
         ["signatures", "same", "holds_by_construction"]),
        ("data.py", "Read-only store; SQLite itself refuses any table outside the contract; strict timestamps; NULL never "
                    "summed as zero; time and row limits for a contributed query, and its rerun on split fact rows",
         ["Store.sums", "Store.read_sql", "Store.time_range", "Store.rerun_on_split_rows"]),
        ("evaluator.py", "Any metric for any segment, window and grouping, always from sums; any metric from a table of sums",
         ["Evaluator.sums", "Evaluator.derive", "Evaluator.frame", "Evaluator.series"]),
        ("validate.py", "Checks of the graph against the data: coverage, identity, SQL against formula, additivity, "
                        "bounds; each marked data or definition",
         ["validate_graph", "summarize"]),
        ("inspect_db.py", "Prints tables, row counts and the time range of a database", ["main"]),
    ]),
    ("Monitor", [
        ("clock.py", "The clock (newest data hour plus 1 h) and the arrival gate: which sources have not arrived for "
                     "an hour; with as_of it replays history without looking ahead",
         ["Clock.newest_hour", "Clock.now", "Clock.late"]),
        ("stats.py", "How surprising a rate is, given its counts: the exact two-sided binomial tail for a proportion, the "
                     "residual against its historical spread for any other ratio",
         ["binomial_surprise", "ratio_surprise", "robust_scale"]),
        ("monitor.py", "Every tick: the expected rate of every watched series, the test of the newest hours, the hours "
                       "held for late data, the watch after a release; returns alerts",
         ["Monitor.tick", "Monitor.rewatch", "watch_list", "Config", "Alert"]),
    ]),
    ("Investigator", [
        ("investigate.py", "For an incident: cause or symptom, the place, the cost, every explanation with its evidence, "
                           "the trail and the report text",
         ["Investigator.report", "Investigator.symptoms", "Investigator.localize", "Investigator.explain",
          "Investigator.releases"]),
        ("localize.py", "Scores every place the dimensions can name for one common shift and chooses the place to report",
         ["scan", "choose"]),
        ("attribution.py", "Splits an outcome's change over the atomic factors along every path of the formulas: LMDI, "
                           "with Shapley as the fallback",
         ["attribute", "leaves", "expand"]),
        ("impact.py", "The outcome recomputed through the formulas of the graph with some rates replaced by other values",
         ["tree_metrics", "recompute", "estimate"]),
    ]),
    ("Incidents and the loop", [
        ("incidents.py", "Groups alerts into incidents and keeps each incident in one file from open to resolved; writes "
                         "the explained and pending records",
         ["Book.update", "Incident", "write_json"]),
        ("run.py", "The loop: live next to the simulator's clock, or a replay of a finished database hour by hour",
         ["main", "live", "replay"]),
        ("narrate.py", "With --narrate (live only): a language model writes a short narration of each incident from its "
                       "own evidence; every number is checked against it, and the template report stays",
         ["Narrator.submit", "Narrator.ready", "evidence", "numbers_backed"]),
        ("settings.py", "Runtime settings the console writes (settings.json beside the database): narration on or off and "
                        "the detection thresholds, applied by the live loop without a restart; the monitor's status file",
         ["read", "settings_path", "write_status"]),
    ]),
    ("New metrics", [
        ("extension.py", "The extension agent: takes a definition from the incoming folder, checks it against the data, "
                         "adds the metric to the graph or rejects it, and writes the answer file",
         ["Extension.poll", "find_formulas", "compare", "identities", "export"]),
    ]),
]
COMMANDS = {"inspect_db.py": "python -m brain.inspect_db", "run.py": "python -m brain.run"}   # shown for `main`


def lines_of(path: pathlib.Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def module_rows(by_module: dict) -> tuple[str, int, int]:
    on_disk = sorted(p.name for p in (ROOT / "brain").glob("*.py") if p.name != "__init__.py")
    listed = [name for _, mods in GROUPS for name, _, _ in mods]
    assert sorted(listed) == on_disk, f"the module table and brain/ differ: {sorted(set(listed) ^ set(on_disk))}"
    rows = []
    for title, mods in GROUPS:
        rows.append(f'<tr><th colspan="5">{esc(title)}</th></tr>')
        for name, job, api in mods:
            module = importlib.import_module(f"brain.{name[:-3]}")
            shown = []
            for dotted in api:
                obj = module
                for part in dotted.split("."):
                    obj = getattr(obj, part)          # AttributeError: the table names something that is not there
                shown.append(COMMANDS[name] if dotted == "main" else dotted)
            tests = sorted(by_module.get(name[:-3], []), key=lambda t: (t != f"test_{name[:-3]}", t))
            cell = ", ".join(tests[:4]) + (f" and {len(tests) - 4} more" if len(tests) > 4 else "") if tests else "none"
            rows.append(f"<tr><td><code>{name}</code></td><td class=\"num\">{lines_of(ROOT / 'brain' / name)}</td>"
                        f"<td>{esc(job)}</td><td><code>{esc(', '.join(shown))}</code></td><td><code>{esc(cell)}</code></td></tr>")
        if title.startswith("Foundation"):
            files = sorted((ROOT / "brain" / "metrics").glob("*.yaml"))
            direct = [p.stem for p in sorted((ROOT / "tests").glob("test_*.py"))
                      if re.search(r'DEFAULT_METRICS_DIR|"brain" / "metrics"|brain/metrics', p.read_text(encoding="utf-8"))]
            rows.append(f"<tr><td><code>metrics/*.yaml</code></td><td class=\"num\">{sum(lines_of(p) for p in files)}</td>"
                        f"<td>{len(g.specs)} metric definitions in {word(len(files))} files: "
                        f"{esc(', '.join(p.stem.replace('_', ' and ') for p in files))}</td>"
                        f"<td><code>name, formula, decomposition, may_affect, systems, source</code></td>"
                        f"<td><code>{esc(', '.join(direct) or 'none')}</code></td></tr>")
    total = sum(lines_of(p) for p in (ROOT / "brain").glob("*.py"))
    return "\n".join(rows), total, len(on_disk)


# ------------------------------------------------------------------ measured
def place(scope: dict) -> str:
    return " × ".join(str(scope[d]) for d in DIMENSIONS if d in scope) or "the whole market"


def plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def measured() -> dict:
    """The numbers of docs/results.json, counted with the tally of scripts/summarize_evaluation.py."""
    saved = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
    holdout, warmup = set(saved["holdout"]), saved["warmup_hours"]
    runs = saved["runs"]
    fresh = [r for r in runs if r["seed"] in holdout]
    seen = [r for r in runs if r["seed"] not in holdout]
    assert fresh and seen, "docs/results.json has no holdout runs, or nothing else"
    th, ts, ta = tally(fresh, warmup), tally(seen, warmup), tally(runs, warmup)
    mark = lambda r: " (holdout)" if r["seed"] in holdout else ""   # noqa: E731

    def line(t: dict) -> str:
        return (f"<b>{t['detected']}/{t['faults']}</b> faults detected, <b>{t['localized']}/{t['detected']}</b> localized, "
                f"<b>{t['correct']}/{t['detected']}</b> with the cause, <b>{t['false_alarms']}</b> "
                f"false alarm{'' if t['false_alarms'] == 1 else 's'} in <b>{t['watched']:.0f}</b> watched days, "
                f"<b>{t['silent']}/{t['innocents']}</b> innocent events silent")

    def row(name: str, t: dict) -> str:
        return (f"<tr><td>{name}</td><td class=\"num\">{t['runs']}</td><td class=\"num\">{t['detected']}/{t['faults']}</td>"
                f"<td class=\"num\">{t['localized']}/{t['detected']}</td><td class=\"num\">{t['correct']}/{t['detected']}</td>"
                f"<td class=\"num\">{t['false_alarms']}</td><td class=\"num\">{t['watched']:.0f}</td>"
                f"<td class=\"num\">{t['silent']}/{t['innocents']}</td><td class=\"num\">{t['mean_delay']}</td></tr>")

    alarms = [(r, a) for r in runs for a in r.get("false_alarm_incidents") or []]
    assert len(alarms) == ta["false_alarms"], "a false alarm is counted and not itemised in docs/results.json"
    alarm_text = "; ".join(f"<code>{esc(a.get('metric') or 'a metric')}</code> in {esc(place(a.get('segment') or {}))}, "
                           f"seed {r['seed']}{mark(r)}, first seen {esc(a['first_seen'])}"
                           + (f", status {esc(a['status'])}" if a.get("status") else "") for r, a in alarms)
    faults = [(r, p) for r in runs for p in r["per_fault"]]
    missed = [(r, p) for r, p in faults if not p["detected"]]
    misplaced = [(r, p) for r, p in faults if p["detected"] and not p.get("localized")]
    uncaused = [(r, p) for r, p in faults if p["detected"] and not p.get("correct_cause")]
    ties = [(r, p) for r, p in faults if p["detected"] and p.get("correct_cause") and p.get("supported_causes", 1) > 1]

    def which(pairs, kind: bool = True) -> str:
        return "; ".join((f"<code>{esc(p['kind'])}</code> in " if kind else "") + f"{esc(place(p['fault']))}, seed {r['seed']}{mark(r)}"
                         for r, p in pairs)

    notes = []
    if missed:
        notes.append(f"<b>Not detected.</b> {plural(len(missed), 'fault')} of {ta['faults']}: {which(missed)}.")
    if misplaced:
        notes.append(f"<b>Not localized.</b> {plural(len(misplaced), 'detected fault')} of {ta['detected']} "
                     f"{'is' if len(misplaced) == 1 else 'are'} reported in another place than the injected one: {which(misplaced)}.")
    if uncaused:
        kinds = sorted({p["kind"] for _, p in uncaused})
        text = (f"<b>Causes not confirmed.</b> In {len(uncaused)} of the {ta['detected']} detected faults "
                f"({sum(r['seed'] in holdout for r, _ in uncaused)} of them on holdout seeds) the report does not name the "
                f"injected cause as supported. ")
        text += (f"All {len(uncaused)} are <code>{esc(kinds[0])}</code> faults: {which(uncaused, kind=False)}."
                 if len(kinds) == 1 and len(uncaused) > 1 else f"In detail: {which(uncaused)}.")
        if all(p.get("localized") and p.get("supported_causes", 0) == 0 for _, p in uncaused):
            text += (" Each is detected and localized, and its report says that no cause is confirmed yet. Without a release "
                     "to point at, a moved rate counts as the cause only when the second movement that a cause link of the "
                     "graph predicts shows in the data.")
        notes.append(text)
    if alarms:
        notes.append(f"<b>{'One false alarm' if len(alarms) == 1 else f'{len(alarms)} false alarms'}.</b> {alarm_text}. "
                     f"That is {ta['false_alarms']} in {ta['watched']:.0f} watched days over all runs. Watched days leave out "
                     f"the first {warmup:g} hours of each run, the history a replay needs before its first judgement.")
    if ties:
        notes.append(f"<b>{'One tie' if len(ties) == 1 else f'{len(ties)} ties'}.</b> In {len(ties)} of the {ta['correct']} "
                     f"reports with the right cause a second cause is supported beside it: {which(ties)}. A harmless "
                     f"release of the same system in the same place within {word(investigate.RELEASE_BEFORE_HOURS)} hours of "
                     f"the onset is supported next to the real one: a known limit (project README, section 3.5).")
    kinds = sorted({p["kind"] for _, p in faults})
    notes.append(f"<b>What the runs do not test.</b> Holdout means new random seeds of the same simulator: the same "
                 f"{word(len(kinds))} kinds of fault ({', '.join(f'<code>{esc(k)}</code>' for k in kinds)}) and the same "
                 f"scorer. No two events overlap in these schedules, and data that never arrives is not in them. The "
                 f"hand-over of a new metric is not part of the runs; it has its own tests "
                 f"(<code>tests/test_extension.py</code>, {count_tests('test_extension')} test functions).")

    tiles = [
        (f"{th['detected']}/{th['faults']}", "faults detected",
         f"{th['localized']}/{th['detected']} of them in the injected place. Mean delay {th['mean_delay']} h after the first "
         f"affected hour of data."),
        (f"{th['correct']}/{th['detected']}", "with the cause",
         "The report names the injected cause as supported. A cause that is only plausible does not count."),
        (str(th["false_alarms"]), f"false alarm{'' if th['false_alarms'] == 1 else 's'} in {th['watched']:.0f} watched days",
         "An incident that matches no injected fault."),
        (f"{th['silent']}/{th['innocents']}", "innocent events silent",
         "Promos and late data: explained or held, no alert."),
    ]
    days = sorted({r["days"] for r in runs})
    per_run = {(len(r["per_fault"]), r["innocents"]) for r in runs}
    assert len(days) == 1 and len(per_run) == 1, (days, per_run)   # the page says "a run is ..." once for all runs
    return {
        "HOLDOUT_LINE": "holdout: " + line(th) + ' (<a href="results.md">results.md</a>)',
        "TILES": "\n".join(f'    <div><span class="big">{b}</span><span class="what">{esc(w)}</span>'
                           f'<span class="why">{esc(y)}</span></div>' for b, w, y in tiles),
        "RESULT_ROWS": "\n".join([row("Holdout (run once)", th), row("Used while the brain was built", ts), row("All", ta)]),
        "NOT_PERFECT": "\n".join(f"    <li>{n}</li>" for n in notes),
        "N_RUNS": word(len(runs)), "N_HOLDOUT_RUNS": word(len(fresh)), "RUN_DAYS": f"{days[0]:g}",
        "FAULTS_PER_RUN": word(len(runs[0]["per_fault"])), "INNOCENTS_PER_RUN": word(runs[0]["innocents"]),
        "RESULTS_COMMIT": esc(str(saved["commit"])),
    }


# ------------------------------------------------------------------ assemble
def build() -> tuple[str, dict]:
    """(the page as a fragment, the edge counts by kind)."""
    fig1, counts = fig_pipeline()
    test_files, n_tests, by_module = brain_tests()
    rows, total_lines, n_modules = module_rows(by_module)
    untested = [m for m in FIGURE_MODULES if not by_module.get(m)]
    assert not untested, f"the first figure names modules without tests: {untested}"
    counts_rates = sum(w.counts for w in WATCHED)
    calendar_rates = [w.metric for w in WATCHED if w.calendar]
    ext = extension
    values = {
        "STATE": datetime.now().strftime("%d %b %Y, %H:%M"),
        "N_METRICS": len(g.specs), "N_EDGES": len(g.edges), "N_MODULES": n_modules, "N_LINES": f"{total_lines:,}",
        "N_TESTS": n_tests, "N_TEST_FILES": len(test_files),
        "FIG_PIPELINE": fig1, "FIG_GRAPH": fig_graph(), "FIG_TICK": fig_tick(), "FIG_NEW_METRIC": fig_new_metric(),
        "MODULE_ROWS": rows,
        "N_FORMULA": counts["formula"], "N_DEFINITION": counts["definition"], "N_BREAKDOWN": counts["breakdown"],
        "N_MAY": counts["may_affect"], "N_CONTEXT": counts["context"],
        "N_TABLES": word(len(READABLE_TABLES)),
        # the loop
        "N_WATCHED": len(WATCHED), "N_COUNT_RATES": counts_rates, "MIN_RATIO_VOLUME": num(cfg.min_ratio_volume),
        "CALENDAR_RATES": " and ".join(f"<code>{m}</code>" for m in calendar_rates),
        "REREAD_HOURS": cfg.reread_hours, "ACTIVE_HOURS": word(cfg.active_hours), "EPISODE_GAP": word(cfg.episode_gap_hours),
        "MIN_HISTORY_HOURS": cfg.min_history_hours, "MIN_HISTORY_VOLUME": num(cfg.min_history_volume),
        "RESOLVE_HOURS": word(incidents.RESOLVE_AFTER_HOURS), "CLEAR_MARGIN": num(localize.CLEAR_MARGIN),
        "N_ATOMS": len(leaves(g, OUTCOME)),
        "NOISE_TOTAL": NOISE_TOTAL, "NOISE_CELL": NOISE_CELL, "NOISE_RATE": NOISE_RATE,
        # a new metric
        "N_CHECKS": len(CHECKS), "SETTLE_LOOKS": word(ext.SETTLE_LOOKS), "MAX_KB": ext.MAX_BYTES // 1024,
        "SQL_TIMEOUT": num(ext.SQL_TIMEOUT), "SQL_MAX_ROWS": f"{ext.SQL_MAX_ROWS:,}", "MAX_TOLERANCE": sci(ext.MAX_TOLERANCE),
        "DEFAULT_TOLERANCE": sci(DEFAULT_TOLERANCE), "N_CANDIDATES": f"{N_CANDIDATES:,}", "N_COLUMN_METRICS": len(COLUMN_METRICS),
        "PROBE_ROWS": ext.PROBE_ROWS, "SPLIT_HOURS": ext.SPLIT_HOURS, "VALIDATE_DAYS": ext.VALIDATE_DAYS,
        "MAX_ATTEMPTS": word(ext.MAX_ATTEMPTS),
        # validated
        "N_DATA_CHECKS": DATA_CHECKS, "N_DEF_CHECKS": DEFINITION_CHECKS, "VALIDATION_DAYS": VALIDATION_DAYS,
        "MIN_COVERAGE": pct(validate.MIN_COVERAGE),
        **measured(),
    }
    page = (HERE / "brain-architecture.template.html").read_text(encoding="utf-8")
    for k, v in values.items():
        assert "{{" + k + "}}" in page, k
        page = page.replace("{{" + k + "}}", str(v))
    assert "{{" not in page, page[page.index("{{"):][:60]
    return page, dict(counts)


def as_document(page: str) -> str:
    """The fragment inside the document skeleton the tracked page has."""
    head, body = page.split('<main class="wrap">', 1)
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n' + head
            + '</head>\n<body style="margin:0">\n<main class="wrap">' + body + "</body>\n</html>\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fragment", help="write only the page without the document wrapper, to this path "
                                       "(the tracked page is left as it is)")
    args = ap.parse_args(argv)
    page, counts = build()
    document = as_document(page)
    # the full text is held before a file is opened: a failed build leaves the old page as it was
    if args.fragment:
        pathlib.Path(args.fragment).write_text(page, encoding="utf-8", newline="\n")
        print("wrote", args.fragment, len(page), "chars;", counts)
        return 0
    (HERE / "brain-architecture.html").write_text(document, encoding="utf-8", newline="\n")
    print("wrote", HERE / "brain-architecture.html", len(document), "chars;", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
