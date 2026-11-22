"""The system overview page is generated from the code; these tests catch it going stale or losing a section."""
import ast
import html
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _builder():
    spec = importlib.util.spec_from_file_location("build_system_overview", ROOT / "docs" / "build_system_overview.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_page_builds_and_names_every_fault_kind_and_domain():
    from sim.world import CATEGORIES, COUNTRIES, PLATFORMS, CHANNELS
    from ui.domains import DOMAINS
    page = _builder().build()
    for kind in ("payment_failure", "stockout_surge", "search_regression", "ad_budget_exhausted", "promo", "data_delay"):
        assert kind in page, kind
    for _, label, metrics in DOMAINS:
        assert html.escape(label) in page and all(m in page for m in metrics)
    assert str(len(COUNTRIES) * len(PLATFORMS) * len(CATEGORIES) * len(CHANNELS)) in page
    assert "brain-architecture.html" in page and "ground_truth" in page


def test_every_scenario_in_the_table_exists_in_the_code():
    from sim import inject
    b = _builder()
    for name, *_ in b.SCENARIOS:
        assert hasattr(inject, name), f"{name} is in the overview but not in sim/inject.py"


def test_the_committed_page_has_the_same_facts_as_the_code():
    """Not byte for byte (line counts move with every edit); the page must mention the current segment and row counts."""
    committed = (ROOT / "docs" / "system-overview.html").read_text(encoding="utf-8")
    from sim import world
    segments = len(world.COUNTRIES) * len(world.PLATFORMS) * len(world.CATEGORIES) * len(world.CHANNELS)
    assert f"<b>{segments}</b>segments per hour" in committed and f"<b>{28 * 24 * segments:,}</b>rows in 28 days" in committed


# --------------------------------------------------------------------------- the page describes the system as built
def _committed() -> str:
    return (ROOT / "docs" / "system-overview.html").read_text(encoding="utf-8")


def _pages() -> dict:
    """The page as the generator builds it now, and the page as it is committed: a claim must hold in both."""
    return {"built": _builder().build(), "committed": _committed()}


def _picture(page: str) -> str:
    return page[page.index("<svg"):page.index("</svg>")]


def _rows(page: str) -> list[list[str]]:
    """Every table row of the page as a list of cell texts."""
    return [[html.unescape(c) for c in re.findall(r"<t[dh]>(.*?)</t[dh]>", row, flags=re.S)]
            for row in re.findall(r"<tr>(.*?)</tr>", page, flags=re.S)]


def _python_files(folder: str) -> list[str]:
    return sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / folder).glob("*.py") if p.name != "__init__.py")


def _imports(path: Path) -> set[str]:
    """The top-level packages a Python file imports."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module.split(".")[0])
    return found


def test_nothing_on_the_page_is_called_planned_or_unfinished():
    for which, page in _pages().items():
        low = page.lower()
        for stale in ("planned", "not finished", "until the monitor writes"):
            assert stale not in low, f"{which} page still says {stale!r}"
        # a dashed box or arrow was the sign for "not built yet"; everything on the picture exists
        assert 'class="box dashed"' not in page and 'class="arrow dashed"' not in page, which


def test_the_picture_names_the_parts_of_the_brain_and_the_files_it_writes():
    for which, page in _pages().items():
        picture = _picture(page)
        for part in ("monitor", "investigator", "extension agent", "incidents/*.json"):
            assert part in picture, f"{which} picture does not name {part!r}"
            assert part in page.replace(picture, ""), f"{which} text does not name {part!r}"


def test_the_new_metric_path_is_on_the_picture_and_in_the_text():
    """The console writes incoming_metrics/<name>.yaml, the brain answers in <name>.result.json and writes the graph
    export again, and the console draws the graph from that export."""
    for which, page in _pages().items():
        picture = html.unescape(_picture(page))
        for name in ("incoming_metrics/", "<name>.yaml", "<name>.result.json", "graph.json"):
            assert name in picture, f"{which} picture does not show {name!r}"
        text = html.unescape(page.replace(_picture(page), ""))
        for name in ("incoming_metrics/<name>.yaml", "incoming_metrics/<name>.result.json", "Add refund_rate", "GRAPH_JSON"):
            assert name in text, f"{which} text does not mention {name!r}"


def test_scoring_says_which_command_writes_which_file(tmp_path):
    from sim import score as sc
    clean = _builder().facts()["clean_days"]
    # two readings of sim/score.py that do not go through the generator: the docstring, and what generate() refuses
    assert f"{clean} clean days first" in sc.generate.__doc__
    with pytest.raises(ValueError):
        sc.generate(tmp_path / "refused.db", days=clean)
    assert not list(tmp_path.iterdir()), "generate() is refused before any file is written"
    for which, page in _pages().items():
        for name in ("scripts/summarize_evaluation.py", "docs/results.md", "data/eval-results.md", "eval_incidents",
                     f"{clean} clean days"):
            assert name in page, f"{which} page does not mention {name!r}"
        for stale in ("writes results.md", "after a clean week"):
            assert stale not in page, f"{which} page still says {stale!r}"


def test_the_measured_numbers_are_those_of_results_json():
    """Computed here from docs/results.json with the summary script's own tally; nothing is typed."""
    from scripts.summarize_evaluation import tally
    saved = json.loads((ROOT / "docs" / "results.json").read_text(encoding="utf-8"))
    holdout = [r for r in saved["runs"] if r["seed"] in set(saved["holdout"])]
    assert holdout and len(holdout) < len(saved["runs"])

    def cells(t):
        return [str(t["runs"]), f"{t['detected']}/{t['faults']}", f"{t['localized']}/{t['detected']}",
                f"{t['correct']}/{t['detected']}", f"{t['false_alarms']} in {t['watched']:.0f} watched days",
                f"{t['silent']}/{t['innocents']}", t["mean_delay"]]

    expected = {"Holdout": cells(tally(holdout, saved["warmup_hours"])),
                "All": cells(tally(saved["runs"], saved["warmup_hours"]))}
    for which, page in _pages().items():
        rows = _rows(page)
        for label, want in expected.items():
            row = next((r for r in rows if r and r[0].startswith(label)), None)
            assert row is not None, f"{which} page has no row of measured results for {label!r}"
            assert row[1:] == want, f"{which} page, {label}: {row[1:]} is not what docs/results.json gives, {want}"
        assert 'href="results.md"' in page and saved["commit"] in page, which
        assert "simulated" in page and "Holdout means" in page, which


def test_the_module_table_lists_every_module_outside_the_brain():
    built = _builder().build()
    paths = _python_files("sim") + _python_files("ui") + _python_files("scripts")
    paths += sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "ui" / "static").iterdir() if p.is_file())
    paths += sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "ui" / "static" / "v3").iterdir() if p.is_file())
    listed = {r[0] for r in _rows(built) if r}
    assert not [p for p in paths if p not in listed], "modules missing from the table"
    # the main console is design 3 (served at /); the old page and its style are the classic console (/classic)
    jobs = {r[0]: html.unescape(r[1]) for r in _rows(built) if len(r) > 1}
    for name in ("ui/static/v3/index.html", "ui/static/v3/style.css", "ui/static/v3/app.js"):
        assert "main console" in jobs[name].lower(), name
    for name in ("ui/static/index.html", "ui/static/console.css"):
        assert "classic console" in jobs[name].lower(), name
    for which, page in _pages().items():
        for name in ("scripts/summarize_evaluation.py", "scripts/check_guardrails.py"):
            assert name in {r[0] for r in _rows(page) if r}, f"{which} page: {name} is not in the module table"
    # the brain's modules stay on the brain's page; this page gives their number and size
    brain = _python_files("brain")
    size = sum(len((ROOT / p).read_text(encoding="utf-8").splitlines()) for p in brain)
    assert f"{len(brain)} modules" in built and f"{size:,} lines" in built


def test_the_brain_and_the_console_share_no_code():
    """The note under the picture says the two meet only in the database and in files."""
    for p in _python_files("brain"):
        assert not _imports(ROOT / p) & {"sim", "ui"}, f"{p} imports the simulator or the console"
    for p in _python_files("ui"):
        assert "brain" not in _imports(ROOT / p), f"{p} imports the brain"


def test_the_domain_note_says_what_the_code_does():
    from brain.graph import MetricGraph
    from ui.domains import DOMAINS
    specs = MetricGraph.load().specs
    with_field = sum(bool(s.domain) for s in specs.values())
    listed = {m for _, _, metrics in DOMAINS for m in metrics}
    built = html.unescape(re.sub(r"<[^>]+>", "", _builder().build()))
    carried = f"None of the {len(specs)} core metrics" if with_field == 0 else f"{with_field} of the {len(specs)} core metrics"
    assert carried in built
    assert not set(specs) - listed, "a core metric is missing from ui/domains.py: the page says all are listed"
    for which, page in _pages().items():
        assert "would remove the second copy" not in page, which


def test_the_generator_writes_utf8_with_lf_line_ends(tmp_path):
    b = _builder()
    out = tmp_path / "page.html"
    b.write(out)
    data = out.read_bytes()
    assert b"\r" not in data and data.decode("utf-8") == b.build()
    assert [p.name for p in tmp_path.iterdir()] == ["page.html"], "the temporary file was left behind"
