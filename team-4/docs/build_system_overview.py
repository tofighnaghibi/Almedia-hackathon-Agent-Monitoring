"""Build docs/system-overview.html: one page that shows how the whole system fits together.

    python docs/build_system_overview.py

The facts on the page (segments, columns, tables, fault kinds, domains, module sizes, test counts, the defaults and
rules of the scoring, the measured results) are read from the code and from docs/results.json, so the page does not go
stale when they change: run this script again. A fact that can no longer be read stops the build with an error. The
brain (brain/) has its own page, docs/brain-architecture.html; this page names its parts and links to it instead of
repeating it.
"""
from __future__ import annotations

import html
import inspect
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from brain.data import READABLE_TABLES             # noqa: E402
from brain.graph import MetricGraph                # noqa: E402
from scripts.summarize_evaluation import LIMITS, tally   # noqa: E402
from sim import db as simdb                        # noqa: E402
from sim import inject, world                      # noqa: E402
from sim import score as sc                        # noqa: E402
from sim.schema import DIMENSIONS, MEASURES, SOURCES, full_ddl   # noqa: E402
from ui.domains import DOMAINS                     # noqa: E402

OUT = ROOT / "docs" / "system-overview.html"
esc = html.escape


# --------------------------------------------------------------------------- facts read from the code
def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def tests_in(path: Path) -> int:
    return len(re.findall(r"^def test_", path.read_text(encoding="utf-8"), flags=re.M))


def first_line(fn) -> str:
    return (inspect.getdoc(fn) or "").split("\n")[0].strip()


def found(pattern: str, text: str, what: str) -> re.Match:
    """A fact read with a pattern. When the source no longer has it, the build stops instead of printing an old number."""
    m = re.search(pattern, text)
    if not m:
        raise RuntimeError(f"{what}: not found any more; bring docs/build_system_overview.py in line with the source")
    return m


def scoring_facts() -> dict:
    """The defaults and rules of one scoring run, read from sim/score.py itself."""
    gen = inspect.signature(sc.generate).parameters
    clean = found(r"\bfirst = (\d+) \* 24\b", inspect.getsource(sc.generate), "the clean days in sim.score.generate")
    cli = inspect.getsource(sc.main)

    def default(flag: str) -> str:
        return found(rf'add_argument\("--{flag}", default="([^"]+)"', cli, f"the default of --{flag} in sim.score").group(1)

    return {"clean_days": int(clean.group(1)), "eval_faults": gen["faults"].default, "eval_innocents": gen["innocents"].default,
            "eval_days": gen["days"].default, "eval_db": default("db"), "eval_incidents": default("incidents"),
            "eval_out": default("out"), "grace_hours": sc.GRACE.total_seconds() / 3600}


def measured() -> dict:
    """The measured results, from the JSON that docs/results.md is rendered from, with the summary script's own tally."""
    saved = json.loads(read("docs/results.json"))
    runs, warmup, held = saved["runs"], saved["warmup_hours"], set(saved["holdout"])
    fresh = [r for r in runs if r["seed"] in held]
    no_cause: dict[str, int] = {}          # detected faults whose report does not support the right cause, by kind
    for r in runs:
        for p in r["per_fault"]:
            if p["detected"] and not p.get("correct_cause"):
                no_cause[p["kind"]] = no_cause.get(p["kind"], 0) + 1
    means = [text[2:] for text in LIMITS if text.startswith("- Holdout means")]
    if not means:
        raise RuntimeError("scripts/summarize_evaluation.py no longer says what holdout means (LIMITS)")
    return {"commit": saved["commit"], "warmup_hours": warmup, "days": sorted({r["days"] for r in runs}),
            "holdout": tally(fresh, warmup) if fresh else None, "all": tally(runs, warmup), "no_cause": no_cause,
            "holdout_means": means[0]}


def facts() -> dict:
    countries, platforms = world.COUNTRIES, world.PLATFORMS
    cats, chans = world.CATEGORIES, world.CHANNELS
    segments = len(countries) * len(platforms) * len(cats) * len(chans)
    modules = [
        ("sim/world.py", "One hour of the marketplace for every segment: sessions to orders, money, ads, stockouts; seasonality and noise; faults and promos applied"),
        ("sim/schema.py", "The tables and columns (the contract, printed as docs/contracts/schema.sql)"),
        ("sim/db.py", "SQLite access, write or rewrite hours with every injection on record"),
        # "about 3 s": measured by the simulator's author when the backfill was built (about 3 s for 4 countries)
        ("sim/run.py", "Backfill a clean history (28 days in about 3 s)"),
        ("sim/live.py", "Move the simulated clock forward, one hour per tick; with --wall-clock, follow real time"),
        ("sim/clock.py", "Wall-clock mode: bring the database up to the real current hour"),
        ("sim/inject.py", "Faults, promos, late data, and the rollback; each one is recorded in ground_truth"),
        ("sim/score.py", "Generate a history with random faults and innocent events; score one replayed run against ground_truth"),
        ("scripts/summarize_evaluation.py", "Score many replayed runs with the scorer of sim/score.py; write docs/results.md and docs/results.json, holdout seeds on their own line"),
        ("scripts/check_guardrails.py", "Exit code 1 when brain/ names ground_truth or a fault kind, imports sim/ or ui/ or opens SQLite outside brain/data.py, "
                                        "or when a secret, data/ or incidents/ is tracked or in the git history"),
        ("scripts/rehearse_core.py", "Rehearsal in a new folder, without the console: a payment fault is found in its country and resolved after the rollback, "
                                     "a promo raises no alert, late data is reported as pending"),
        # rows for scripts that other pull requests add; a row shows only while its file exists
        ("scripts/baseline_ranking.py", "The plain ranking that docs/results.md sets beside the brain: each hour the most anomalous rate and cell "
                                        "against the same hour of the previous three weeks, no calendar and no investigation"),
        ("scripts/live_check.py", "Unattended live check: the real clock, brain and console-style injections for 36 simulated hours with every "
                                  "kind of event, then eight checks; exit code 1 if one fails"),
        ("scripts/stress_cases.py", "Eleven targeted stress cases (traffic drop, small payment fault, fault during a promo, "
                                    "unannounced mix shift, recommendations faults, missing or late release, sibling places, "
                                    "recurrence, late source), each a seeded 30-day history replayed by the brain and judged "
                                    "from its output files; writes docs/stress.md and docs/stress.json"),
        ("ui/app.py", "The console's API: the main console at /, the classic one at /classic, graph export, incidents, state, "
                      "injection, rollback, new metric, data browser"),
        ("ui/domains.py", "Which metric belongs to which business domain"),
        ("ui/static/v3/index.html", "Main console (/), the investigation canvas: header with clock, counts, domains and Test bench; "
                                    "graph canvas; incident dock; report panel; replay controls"),
        ("ui/static/v3/style.css", "The main console's style, light and dark"),
        ("ui/static/v3/app.js", "The main console's script: draws the graph, the dock and the reports, replays an incident's trail "
                                "on the graph, sends the Test bench's requests"),
        ("ui/static/v3/route.js", "Where each link on the graph leaves and enters its boxes and the curve between, chosen so no "
                                  "link makes a hook wherever boxes are moved; pure geometry, also run by the tests under node"),
        ("ui/static/index.html", "Classic console (/classic): domain selector, graph, incidents, report, DEMO controls"),
        ("ui/static/data.html", "Read-only browser of the simulator tables"),
        ("ui/static/console.css", "Style of the classic console and of the data browser, light and dark"),
    ]
    mods = [(name, job, lines(ROOT / name)) for name, job in modules if (ROOT / name).is_file()]
    tests = [(p.name, tests_in(p)) for p in sorted((ROOT / "tests").glob("test_*.py"))]
    tables = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", full_ddl())
    brain_files = [p for p in sorted((ROOT / "brain").glob("*.py")) if p.name != "__init__.py"]
    specs = MetricGraph.load().specs
    in_a_domain = {m for _, _, metrics in DOMAINS for m in metrics}
    small = found(r"(\d+)% of rows have fewer than (\d+) payment attempts", read("docs/data-granularity.md"),
                  "the share of small rows in docs/data-granularity.md")
    return {
        "countries": countries, "platforms": platforms, "cats": cats, "chans": chans, "segments": segments,
        "measures": MEASURES, "dims": DIMENSIONS, "sources": SOURCES, "domains": DOMAINS, "modules": mods, "tests": tests,
        # 28 days: the history the demo starts from (docs/demo.md, "python -m sim.run --backfill 28d")
        "hours": 28 * 24, "rows": 28 * 24 * segments,
        "inject_docs": {n: first_line(getattr(inject, n)) for n in
                        ("payment_failure", "stockout_surge", "search_regression", "ad_budget_exhausted", "promo", "data_delay", "end_injection")},
        "market_db": simdb.DEFAULT_DB, "tables": tables, "readable": [t for t in tables if t in READABLE_TABLES],
        "brain_modules": len(brain_files), "brain_lines": sum(lines(p) for p in brain_files),
        "core_metrics": len(specs), "with_domain": sum(bool(s.domain) for s in specs.values()),
        "outside_domains": sorted(set(specs) - in_a_domain),
        "small_rows": small.group(1), "small_attempts": small.group(2),
        # the path the demo gives to the brain (--graph-out) and to the console (GRAPH_JSON)
        "graph_out": found(r"\| GRAPH_JSON \| (\S+) \|", read("docs/demo.md"), "GRAPH_JSON in docs/demo.md").group(1),
        "refresh_s": int(found(r"setInterval\(refresh, (\d+)\)", read("ui/static/v3/app.js"), "the console's refresh interval").group(1)) / 1000,
        "calls_a_language_model": any(re.search(r"^\s*(?:import|from)\s+anthropic\b", p.read_text(encoding="utf-8"), flags=re.M)
                                      for folder in ("brain", "sim", "ui", "scripts") for p in (ROOT / folder).glob("*.py")),
        **scoring_facts(), "measured": measured(),
    }


# the faults: what changes in the data, what is left as context, what the brain should report
SCENARIOS = [
    ("payment_failure", "Fault", "Payment success rate falls in one country and platform", "release: payments-sdk",
     "orders, payment success rate, the country and platform, the release"),
    ("stockout_surge", "Fault", "More views land on out-of-stock listings in one category; add-to-cart rate falls, feed clicks rise", "none",
     "orders, add-to-cart rate, the category, stockout rate"),
    ("search_regression", "Fault", "Clicks per search fall in one country, so fewer product views", "release: search",
     "orders, views per session, search clicks per search, the country, the release"),
    ("ad_budget_exhausted", "Fault", "Sponsored impressions per session jump, then collapse; traffic unchanged", "release: pacing",
     "ad spend, ad load (not sessions), the country"),
    ("promo", "Innocent", "Traffic and orders rise in one country", "calendar: promo",
     "explained by the calendar, no alert"),
    ("data_delay", "Innocent", "One source arrives late; the facts stay correct", "ingestion_log only",
     "held as pending, no alert"),
]

# What the README says is not built ("Not built" at the top and in sections 3.3 to 3.5 and 3.7), checked against the code.
NOT_BUILT = [
    "A euro threshold for alerts. A change is judged by its size against the rate's own history, not by what it costs.",
    "Judging volumes such as sessions. Only rates are watched.",
    "Formulas with constants, or with more than two metrics, in the search for the formula of a new metric.",
    "Proposing cause links for a new metric. A definition can declare such links itself.",
    "A range around the cost of an incident. The cost is one estimate, and it is noisy for a short incident in a small place.",
]


# --------------------------------------------------------------------------- the picture
def box(x, y, w, h, title, sub, strong=False, part=False, tail=""):
    """A box with a bold title and muted lines under it. `tail`: muted text after the title, on its line (the path or
    the command). `part`: a smaller box, used inside the brain and the console and for the files between them."""
    first, step, left = (17, 16, 10) if part else (22, 20, 12)
    after = f'<tspan class="s" dx="8">{esc(tail)}</tspan>' if tail else ""
    out = [f'<rect class="{"box strong" if strong else "box"}" x="{x}" y="{y}" width="{w}" height="{h}" rx="{6 if part else 8}"/>',
           f'<text class="t" x="{x + left}" y="{y + first}">{esc(title)}{after}</text>']
    for i, line in enumerate(sub):
        out.append(f'<text class="s" x="{x + left}" y="{y + first + step + 16 * i}">{esc(line)}</text>')
    return "\n".join(out)


def file_line(x, y, name, what):
    """One line of a folder box: the file name, then what the file holds."""
    return f'<text class="s" x="{x}" y="{y}"><tspan class="f">{esc(name)}</tspan><tspan dx="8">{esc(what)}</tspan></text>'


def arrow(x1, y1, x2, y2, label="", lx=None, ly=None, anchor="start"):
    out = f'<line class="arrow" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" marker-end="url(#ah)"/>'
    if label:
        x = lx if lx is not None else (x1 + x2) / 2
        y = ly if ly is not None else (y1 + y2) / 2 - 6
        for i, line in enumerate(label.split("\n")):
            out += f'<text class="l" text-anchor="{anchor}" x="{x}" y="{y + 13 * i}">{esc(line)}</text>'
    return out


def caption(x, y, name, rest):
    return f'<text class="cap" x="{x}" y="{y}">{esc(name)}<tspan dx="12">{esc(rest)}</tspan></text>'


def diagram(f) -> str:
    """Two bands. The live run: simulator, database, brain, the files between brain and console, console.
    The scoring run: the same brain replayed over a generated history.
    The texts are sized to fit their boxes in Inter and in the fonts the page falls back to. After a change of a text
    or a box, look at the page at a width of 1000 px in both themes."""
    readable = f["readable"]
    hidden = [t for t in f["tables"] if t not in readable]
    if "ground_truth" not in hidden:
        raise RuntimeError("brain/data.py lets the brain read ground_truth: the picture and the note under it would be wrong")
    parts = [
        '<svg viewBox="0 0 1000 672" role="img" aria-label="How the parts fit together: the live run and the scoring run">',
        '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,1 L10,5 L0,9 z" class="head"/></marker></defs>',

        # ---- the live run, row 1: what writes the database (all boxes centred on y = 93)
        caption(20, 24, "LIVE RUN", "the simulator, the brain and the console are separate processes"),
        box(20, 56, 148, 74, "live clock", ["one hour per tick", "sim/live.py"]),
        box(232, 40, 186, 106, "world", [f"{f['segments']} segments per hour", "funnel, rhythm, noise,", "injections applied", "sim/world.py"], strong=True),
        box(482, 40, 248, 106, "database",
            [readable[0], ", ".join(readable[1:]), "ground_truth (the hidden answer)", ", ".join(t for t in hidden if t != "ground_truth")],
            strong=True, tail=f["market_db"]),
        box(794, 48, 186, 90, "inject", ["records faults, promos,", "late data and rollbacks", "sim/inject.py"]),
        arrow(168, 93, 232, 93, "next hour", lx=200, ly=85, anchor="middle"),
        arrow(418, 93, 482, 93, "hours", lx=450, ly=85, anchor="middle"),
        arrow(794, 93, 730, 93),

        # ---- row 2: the brain, the files it exchanges with the console, the console (parts on shared rows)
        box(20, 216, 300, 308, "brain/", [], strong=True, tail="python -m brain.run"),
        box(32, 250, 276, 60, "extension agent", ["checks a new metric on the data", "and finds its place in the graph"], part=True),
        box(32, 320, 276, 42, "metric graph", ["metrics, formulas, possible causes"], part=True),
        box(32, 370, 276, 42, "monitor", ["judges every watched rate each hour"], part=True),
        box(32, 420, 276, 42, "investigator", ["finds the place, the cost, the cause"], part=True),
        box(32, 470, 276, 42, "incident files", ["open, ongoing, resolved in one file"], part=True),

        box(372, 242, 276, 70, "incoming_metrics/", [], part=True),
        file_line(382, 283, "<name>.yaml", "a new metric definition"),
        file_line(382, 301, "<name>.result.json", "the brain's answer"),
        box(372, 320, 276, 42, f["graph_out"], ["graph export, the file in $GRAPH_JSON"], part=True),
        box(372, 470, 276, 42, "incidents/*.json", ["also explained and pending records"], part=True),

        box(700, 216, 280, 308, "console", [], strong=True, tail="ui/"),
        box(712, 250, 256, 60, "Test bench (DEMO)", ["inject a fault, roll it back,", "Add refund_rate (a new metric)"], part=True),
        box(712, 320, 256, 42, "graph canvas", ["the formula tree, by domain"], part=True),
        box(712, 370, 256, 42, "data page", ["the tables, read only"], part=True),
        box(712, 420, 256, 92, "incident dock and report", ["what changed, where, what it cost,", "causes, what remains uncertain,", "the trail, replayed on the graph"], part=True),

        # a new metric goes left (yaml), its answer goes right (result.json); graph export and incidents go right
        arrow(700, 279, 648, 279), arrow(372, 279, 320, 279),
        arrow(320, 297, 372, 297), arrow(648, 297, 700, 297),
        arrow(320, 341, 372, 341), arrow(648, 341, 700, 341),
        arrow(320, 491, 372, 491), arrow(648, 491, 700, 491),

        # ---- between the rows
        arrow(500, 146, 296, 216, f"read only,\n{len(readable)} tables", lx=418, ly=194),
        arrow(712, 146, 760, 216, "clock, tables", lx=724, ly=190, anchor="end"),
        arrow(900, 216, 900, 138, "Test bench", lx=890, ly=181, anchor="end"),

        # ---- the scoring run
        caption(20, 560, "SCORING RUN", "its own database and incident folder; the brain is the same code"),
        box(20, 574, 205, 84, "generate", ["random faults and innocent", f"events after {f['clean_days']} clean days", f"writes {f['eval_db']}"],
            tail="sim/score.py"),
        box(251, 574, 215, 84, "replay", ["the same brain, hour by hour", f"writes {f['eval_incidents']}/*.json"], tail="brain/run.py"),
        box(492, 574, 215, 84, "score", ["one run against ground_truth", f"writes {f['eval_out']}"], tail="sim/score.py"),
        box(733, 574, 247, 84, "summarize", ["scripts/summarize_evaluation.py", "reads the runs, same scorer,", "holdout apart; docs/results.md"]),
        arrow(225, 616, 251, 616), arrow(466, 616, 492, 616),
        '</svg>',
    ]
    return "\n".join(p for p in parts if p)


# --------------------------------------------------------------------------- the page
def table(headers, rows, fit=False):
    """`fit`: cells stay on one line (a table of short numbers); the block scrolls sideways on a narrow screen."""
    h = "".join(f"<th>{esc(x)}</th>" for x in headers)
    body = "".join("<tr>" + "".join(f"<td>{esc(str(c))}</td>" for c in r) + "</tr>" for r in rows)
    return f"<div class=\"tw{' fit' if fit else ''}\"><table><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table></div>"


def measured_html(m: dict) -> str:
    def row(label, t):
        return (label, t["runs"], f"{t['detected']}/{t['faults']}", f"{t['localized']}/{t['detected']}", f"{t['correct']}/{t['detected']}",
                f"{t['false_alarms']} in {t['watched']:.0f} watched days", f"{t['silent']}/{t['innocents']}", t["mean_delay"])

    everything, fresh = m["all"], m["holdout"]
    days = m["days"]
    span = f"{days[0]:g} days" if len(days) == 1 else f"{days[0]:g} to {days[-1]:g} days"
    brain = f", replayed by the brain at commit <code>{esc(m['commit'])}</code>" if m["commit"] else ""
    rows = ([row("Holdout seeds", fresh)] if fresh else []) + [row("All runs", everything)]
    notes = ["Localized and correct supported cause are counted among the detected faults; a cause counts only when the report "
             "names it as <code>supported</code>. The delay is counted in hours from the first affected hour."]
    if m["warmup_hours"]:
        notes.append(f"Watched days leave out the first {m['warmup_hours']:g} hours of each run, before the replay's first judgement.")
    lacking = everything["detected"] - everything["correct"]
    if lacking:
        kinds = ", ".join(f"{kind} ({n})" for kind, n in sorted(m["no_cause"].items()))
        notes.append(f"Over all runs, {lacking} detected fault{'s have' if lacking > 1 else ' has'} a report that does not support "
                     f"the right cause: {esc(kinds)}.")
    if everything["ties"]:
        notes.append(f"In {everything['ties']} of the {everything['correct']} reports with the right cause a second cause is supported beside it.")
    out = [f'<p><a href="results.md">docs/results.md</a> holds the results of {everything["runs"]} scored runs of {span} each{brain}. '
           "All data is simulated: these are checks on a simulator, not production performance.</p>",
           table(["Seeds", "Runs", "Faults detected", "Localized", "Correct supported cause", "False alarms", "Innocent events silent", "Mean delay (h)"],
                 rows, fit=True),
           f'<p class="note">{" ".join(notes)}</p>']
    if fresh:
        others = everything["runs"] - fresh["runs"]
        # which seeds are holdout is a statement of whoever ran the summary script (its --holdout); no script can check it
        out.append(f"<p>{esc(m['holdout_means'])} The {fresh['runs']} holdout seeds were generated and replayed once for that page: no version "
                   "of the brain was run on them before. The team states this; the scripts cannot check it. "
                   f"The other {others} seeds were also run while the brain was built, so their numbers are not "
                   'independent. The other limits are listed at the end of <a href="results.md">results.md</a>.</p>')
    return "\n".join(out)


def build() -> str:
    """The page as text. Numbers come from facts(). What is typed in the sentences below was read in the code:
    - one routine release per day in a scoring history: sim/db.py, add_release_context (one row per day);
    - promos and late data as the innocent events of a scoring run: sim/score.py, generate;
    - the example incident only while the incidents folder is missing: ui/app.py, _incidents, and the brain makes the
      folder at its start: brain/incidents.py, Book.__init__;
    - the graph export at the start and after each change of the graph: brain/run.py, main, and brain/extension.py;
    - the console never reads a node's `domain`: ui/static/v3/app.js (and the classic ui/static/index.html) take the
      domains from /api/domains only;
    - the main console's canvas, dock, report panel, replay and Test bench: ui/static/v3/index.html and app.js."""
    f = facts()
    css = read("ui/static/console.css")
    total_tests = sum(n for _, n in f["tests"])
    dims = ", ".join(f"{d} ({n})" for d, n in zip(f["dims"], (len(f["countries"]), len(f["platforms"]), len(f["cats"]), len(f["chans"]))))
    scen = table(["Name", "Kind", "What changes in the data", "Left as context", "What the brain should report"],
                 [(a, b, c, d, e) for a, b, c, d, e in SCENARIOS])
    mods = table(["Module", "Job", "Lines"], [(n, j, l) for n, j, l in f["modules"]])
    tests = table(["Test file", "Tests"], list(f["tests"]) + [("total", total_tests)])
    domains = table(["Domain", "Metrics"], [(label, ", ".join(ms)) for _, label, ms in f["domains"]])
    readable = ", ".join(f"<code>{esc(t)}</code>" for t in f["readable"])
    carried = (f"None of the {f['core_metrics']} core metrics carries it today" if f["with_domain"] == 0
               else f"{f['with_domain']} of the {f['core_metrics']} core metrics carry it today")
    in_table = (f"all {f['core_metrics']} are listed in it" if not f["outside_domains"]
                else f"{len(f['outside_domains'])} of the core metrics are not listed in it ({esc(', '.join(f['outside_domains']))})")
    no_model = "" if f["calls_a_language_model"] else " No module in brain/, sim/, ui/ or scripts/ calls a language model."
    not_built = "".join(f"<li>{esc(item)}{no_model if item.startswith('Narration') else ''}</li>" for item in NOT_BUILT)
    page_css = """
    .page { max-width: 1100px; }
    h2 { font-size: 12px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); margin: 48px 0 16px; }
    p { max-width: 72ch; margin: 0 0 16px; }
    ul { max-width: 72ch; margin: 0 0 16px; padding-left: 20px; }
    li { margin: 0 0 8px; }
    .lead { font-size: 16px; max-width: 70ch; }
    .meta { color: var(--muted); font-size: 12px; letter-spacing: .04em; margin-bottom: 8px; }
    .stats { display: flex; flex-wrap: wrap; gap: 8px 32px; margin: 16px 0 0; }
    .stats b { font-size: 20px; font-weight: 700; margin-right: 8px; }
    .tw { background: var(--block); border-radius: 8px; overflow-x: auto; }
    .tw.fit td { white-space: nowrap; }
    table { border-collapse: collapse; width: 100%; font-size: 13px; }
    th, td { padding: 8px 16px; text-align: left; vertical-align: top; }
    th { color: var(--muted); font-size: 12px; font-weight: 700; letter-spacing: .04em; }
    .dia { background: var(--block); border-radius: 8px; padding: 16px; overflow-x: auto; }
    svg { width: 100%; min-width: 720px; height: auto; }
    .box { fill: var(--bg); stroke: var(--edge); stroke-width: 1.3; } .box.strong { stroke: var(--ink); stroke-width: 1.6; }
    .t { fill: var(--ink); font: 700 13px var(--ui); } .s { fill: var(--muted); font: 400 12px var(--ui); } .l { fill: var(--muted); font: 400 11px var(--ui); }
    .f { fill: var(--ink); }
    .cap { fill: var(--muted); font: 700 11px var(--ui); letter-spacing: .06em; } .cap tspan { font-weight: 400; letter-spacing: 0; }
    .arrow { stroke: var(--muted); stroke-width: 1.4; fill: none; } .head { fill: var(--muted); }
    .note { color: var(--muted); font-size: 12px; margin-top: 8px; }
    code { font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
    """
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>System overview</title>
<script>var t = new URLSearchParams(location.search).get('theme'); if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;</script>
<style>
{css}
{page_css}
</style>
</head>
<body>
<div class="page">
<header class="top"><div>
  <div class="meta">EYE OF THE SYSTEM · HACKLAB #2 BERLIN · ALMEDIA TRACK</div>
  <h1>How the whole system fits together</h1>
</div></header>
<p class="lead">A simulated marketplace produces hourly numbers. The brain (<code>brain/</code>) reads them, notices a change,
finds the place and the cause and writes an incident report. It also takes in new metric definitions while it runs. A console
shows what the brain found. Around them: a way to put faults into the world, and scripts that score the brain against the
truth. This page covers the world, the console and the scoring; the brain has its own page:
<a href="brain-architecture.html">brain-architecture.html</a>.</p>
<div class="stats"><span><b>{f['segments']}</b>segments per hour</span><span><b>{f['rows']:,}</b>rows in 28 days</span>
<span><b>{len(SCENARIOS)}</b>kinds of injection</span><span><b>{total_tests}</b>test functions</span></div>

<h2>The parts</h2>
<div class="dia">
{diagram(f)}
</div>
<p class="note">Every part on the picture exists in the code and has tests. The middle column of the live run shows the files
that the brain and the console exchange. The brain opens the database read-only, and SQLite lets it
read {len(f['readable'])} tables: {readable}. The table <code>ground_truth</code> is refused to it. The brain imports nothing from
<code>sim/</code> or <code>ui/</code>, and the console imports nothing from <code>brain/</code>: they meet only in the database
and in those files. The console shows the example incident from
<code>docs/contracts/incident_example.json</code>, marked as an example, only while the incidents folder does not exist. The brain
creates the folder when it starts; from then on an empty folder means that nothing was found.</p>
<p>The brain is one process, <code>python -m brain.run</code>. The metric graph holds the metrics, their formulas and the possible
causes; it is validated against the data before its export is written. Every hour the monitor judges the newest hours of every
watched rate. For each open incident the investigator finds the place, the cost in EUR and in orders, and the cause, and records
every question and its answer in a trail. Each incident is one file, <code>incidents/*.json</code>, that is updated in place while
the incident is open, ongoing and then resolved. The extension agent takes in new metrics while the brain runs
(see <a href="#console">the console</a>). The brain has {f['brain_modules']} modules and {f['brain_lines']:,} lines;
<a href="brain-architecture.html">brain-architecture.html</a> describes them.</p>

<h2>What the world contains</h2>
<p>One row per hour and segment. A segment is a combination of {esc(dims)}: {f['segments']} segments.
The purchase funnel is built from counts and rates, so the graph's formulas hold exactly in the data:
sessions, product views, add to cart, checkouts, payment attempts, payment successes, orders, then money.</p>
<p>Columns ({len(f['measures'])}): {esc(', '.join(f['measures']))}. Sources that report data: {esc(', '.join(f['sources']))}.</p>
<p>Daily and weekly rhythm, a slow trend, one shared shock per country and hour, and the noise of small counts.
At the row level {f['small_rows']}% of rows have fewer than {f['small_attempts']} payment attempts, so a single row can show a rate of 0.0 or 1.0 by chance;
see <a href="data-granularity.md">data-granularity.md</a> for what each level of aggregation can and cannot show.</p>

<h2>Faults and innocent changes</h2>
{scen}
<p class="note">Every injection is written to <code>ground_truth</code> with its full settings, so it can be replayed. A rollback sets the end of
an injection; for a fault that came with a release (payment, search, ad budget) it also writes a rollback release. The same fault with the same settings cannot be recorded twice.
What the brain reported for each kind of fault is in <a href="results.md">results.md</a>.</p>

<h2>Why one change moves nothing else</h2>
<p>Every value in an hour gets its own uniform random number, drawn up front, and is turned into a count through the inverse
distribution. A change to one segment's rate therefore changes only that segment's values: the random numbers of every other
segment stay where they were. Backfill, live mode and replaying an injection give identical rows wherever the injection does not
reach. The tests compare whole tables row by row to prove it.</p>

<h2 id="console">The console</h2>
<p>The main console (<code>/</code>, <code>ui/static/v3/</code>) is a canvas. The header holds the clock, the incident counts,
the domain selector and the <b>Test bench</b>. The graph fills the page and can be dragged and zoomed; it shows the formula tree,
coloured by incident status, and picking a domain keeps that domain's boxes and their path to the top. The incidents are cards
in a dock along the bottom, live ones first. A click on a card lights the incident's path on the graph, with the change of each
moved metric and "held" on each factor that stayed normal, and opens its report in a panel on the right; a click on a metric
opens its definition there. <b>Replay the investigation</b> walks the incident's trail on the graph one step at a time: each
step rings the metrics it touched, leaves a numbered pin and shows its result, and the same step is lit in the report's trail.
The Test bench, marked DEMO, holds the controls that write simulated faults, end them (a rollback) and hand in a new metric.
The classic console (<code>/classic</code>, <code>ui/static/index.html</code>) shows the same data and controls with the graph
and the incidents side by side and the DEMO bar folded at the top. The Data page browses the tables read-only.</p>
<p>The console draws the graph from the brain's graph export, the JSON file that the variable <code>GRAPH_JSON</code> names.
The demo sets it to <code>{esc(f['graph_out'])}</code>. The brain writes that file when it starts (<code>--graph-out</code>) and
again whenever the graph changes. Without the variable the console reads the tracked example export
<code>docs/contracts/graph.json</code>, which does not change at run time. The console asks for the graph, the incidents and
the clock every {f['refresh_s']:g} seconds.</p>
<p>A new metric at run time. The button <b>Add refund_rate</b> in the Test bench sends a definition (a name and a query; the
agent finds the formula itself)
to the console's API, which saves it as <code>incoming_metrics/&lt;name&gt;.yaml</code>. The brain's extension agent looks into
that folder on every poll, checks the definition against the data and answers in
<code>incoming_metrics/&lt;name&gt;.result.json</code>: a status (<code>validated</code>, <code>tentative</code> or
<code>rejected</code>), one sentence for a person, and every check with its numbers. When the metric is accepted, the brain writes
the graph export again, and a rate of two columns is watched from the next tick. The console shows the sentence and, on its
next refresh, the new graph.</p>
{domains}
<p class="note">The console takes this table from <code>ui/domains.py</code>. A metric file in <code>brain/metrics</code> may carry a
<code>domain</code> field: <code>brain/metric_spec.py</code> accepts it and <code>brain/graph.py</code> puts it into the graph export.
{carried} and the console does not read it, so this table is the only copy; {in_table}. A metric that is not listed, such as
one added at run time, is shown under Other.</p>

<h2>Scoring</h2>
<p><code>python -m sim.score generate</code> builds the database of one scoring run (default <code>{esc(f['eval_db'])}</code>):
{f['eval_faults']} random faults, {f['eval_innocents']} innocent events (promos and late data) and one routine release per day
over {f['eval_days']} days. It leaves {f['clean_days']} clean days before the first event. After them every event has a slot of
its own, so two events never overlap.</p>
<p><code>python -m brain.run --replay --db {esc(f['eval_db'])} --incidents {esc(f['eval_incidents'])} --fresh</code> walks that
database hour by hour, as if it were live, and never reads a row from the future. It writes its incident files to a folder of
their own; the live <code>incidents/</code> folder is never used for scoring.</p>
<p><code>python -m sim.score score</code> scores one run. It compares the incident files (default folder
<code>{esc(f['eval_incidents'])}</code>) with <code>ground_truth</code> and writes the report of that run to
<code>{esc(f['eval_out'])}</code>: faults detected, delay, localization, the right cause, false alarms, and how many innocent
events stayed silent. An incident counts for a fault only when it is first seen inside the fault's window (up to
{f['grace_hours']:g} hours after its end) in a place that does not contradict the fault's, and it counts for one fault only.
Records with the status <code>explained</code> or <code>pending</code> are neither a detection nor a false alarm.</p>
<p><code>python -m scripts.summarize_evaluation</code> (<code>scripts/summarize_evaluation.py</code>) scores many runs with the
same function. It writes <code>docs/results.md</code> and, beside it, <code>docs/results.json</code> with everything that page is
rendered from. Holdout seeds get a line of their own. False alarms are counted per watched day there: the days of data
without the hours of each run that pass before the replay's first judgement.</p>

<h2>Measured</h2>
{measured_html(f['measured'])}

<h2>Not built</h2>
<ul>{not_built}</ul>

<h2>Modules</h2>
<p>The simulator, the console and the scripts. The brain's {f['brain_modules']} modules ({f['brain_lines']:,} lines) are described on
<a href="brain-architecture.html">its own page</a>.</p>
{mods}

<h2>Tests</h2>
{tests}
<p class="note">The table counts every test file in the repository, the brain's included.</p>
<p class="note">Generated by <code>docs/build_system_overview.py</code> from the code and from <code>docs/results.json</code>. All data is simulated.</p>
</div>
</body>
</html>
"""


def write(path: Path = OUT) -> Path:
    """UTF-8 with LF line ends on every platform: the page is tracked with LF, and a plain write_text gives CRLF on
    Windows. The whole text is on disk in a temporary file before it takes the page's place, so a build that fails
    never leaves the page empty."""
    text = build()
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    try:
        with open(temp, "w", encoding="utf-8", newline="\n") as out:
            out.write(text)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return path


if __name__ == "__main__":
    write()
    print(f"{OUT.relative_to(ROOT)}: {OUT.stat().st_size} bytes")
