"""docs/brain-architecture.html describes the brain as it is built; these tests catch the committed page going stale.

The page is read as committed: nothing is built here and the generator is not imported, so a stale page fails.
Rebuild it with `python docs/build_brain_architecture.py`.
"""
import ast
import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "docs" / "brain-architecture.html"


def _page() -> str:
    return PAGE.read_text(encoding="utf-8")


def _text(page: str) -> str:
    """What a reader sees: the style block and the tags removed, entities decoded, whitespace collapsed."""
    body = re.sub(r"<style>.*?</style>", " ", page, flags=re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", body)).split())


def _modules() -> list[str]:
    return sorted(p.name for p in (ROOT / "brain").glob("*.py") if p.name != "__init__.py")


def _holdout() -> dict:
    """The holdout line of docs/results.json, counted here from the runs, not taken from the page's generator."""
    saved = json.loads((ROOT / "docs" / "results.json").read_text(encoding="utf-8"))
    runs = [r for r in saved["runs"] if r["seed"] in set(saved["holdout"])]
    faults = [p for r in runs for p in r["per_fault"]]
    found = [p for p in faults if p["detected"]]
    return {
        "faults": len(faults), "detected": len(found),
        "localized": sum(bool(p.get("localized")) for p in found),
        "correct": sum(bool(p.get("correct_cause")) for p in found),
        "false_alarms": sum(r["false_alarms"] for r in runs),
        "watched": sum(r["days"] for r in runs) - len(runs) * saved["warmup_hours"] / 24,
        "innocents": sum(r["innocents"] for r in runs), "silent": sum(r["innocents_silent"] for r in runs),
    }


def test_nothing_on_the_page_is_called_planned():
    page = _page().lower()   # the whole file: the figures' labels for screen readers count as well
    assert "planned" not in page
    assert "next milestone" not in page and "will use" not in page


def test_the_module_table_has_a_row_for_every_module_of_the_brain():
    page = _page()
    modules = _modules()
    assert len(modules) >= 18
    missing = [m for m in modules if f"<td><code>{m}</code></td>" not in page]
    assert not missing, f"no table row for {missing}; rebuild the page"
    assert f"<b>{len(modules)}</b> modules" in page


def test_the_page_names_the_parts_that_are_built():
    text = _text(_page()).lower()
    for part in ("monitor", "investigator", "extension agent"):
        assert part in text, part
    for section in ("from data to incident", "one tick of the loop", "a new metric", "measured", "not built"):
        assert section in text, section


def test_the_header_gives_the_holdout_numbers_of_the_results_file():
    page = _page()
    text = _text(page)
    h = _holdout()
    assert h["faults"] > 0 and h["watched"] > 0
    alarms = f"{h['false_alarms']} false alarm{'' if h['false_alarms'] == 1 else 's'}"
    for phrase in (
        f"{h['detected']}/{h['faults']} faults detected",
        f"{h['localized']}/{h['detected']} localized",
        f"{h['correct']}/{h['detected']} with the cause",
        f"{alarms} in {h['watched']:.0f} watched days",
        f"{h['silent']}/{h['innocents']} innocent events silent",
    ):
        assert phrase in text, f"{phrase!r} is not on the page; rebuild it"
    assert 'href="results.md"' in page


def test_the_new_metric_figure_names_every_check_of_the_extension_agent():
    """The checks and their order are the list in the module docstring of brain/extension.py."""
    doc = ast.get_docstring(ast.parse((ROOT / "brain" / "extension.py").read_text(encoding="utf-8")))
    checks = re.findall(r"^  ([a-z]+) {2,}\S", doc, flags=re.M)
    assert len(checks) >= 11 and checks[0] == "read" and checks[-1] == "monitoring"
    text = _text(_page())
    positions = [text.find(f"{i} · {name}") for i, name in enumerate(checks, 1)]
    assert -1 not in positions, [c for c, p in zip(checks, positions) if p == -1]
    assert positions == sorted(positions)
    for status in ("validated", "tentative", "rejected"):
        assert status in text


def test_the_committed_page_is_what_the_generator_makes_now():
    """Any drift (a threshold in Config, a count, an entry point, the measured numbers) changes the build;
    only the time stamp may differ. Rebuild with `python docs/build_brain_architecture.py` after a change."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_brain_architecture", ROOT / "docs" / "build_brain_architecture.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                       # the generator writes nothing when imported
    page, _ = mod.build()
    # the time stamp, and the count of test functions, which grows with any test another change adds
    moving = re.compile(r"state of [^<]*|<b>\d+</b> test functions")
    committed = (ROOT / "docs" / "brain-architecture.html").read_text(encoding="utf-8")
    assert moving.sub("-", mod.as_document(page)) == moving.sub("-", committed)
    assert re.search(r"<b>\d+</b> test functions in the <b>\d+</b> test files", committed)

