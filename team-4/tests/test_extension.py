"""The extension agent (README 3.3, issue 3, demo scenario S4): a metric arrives as a definition file,
the brain checks it against the data, finds its place in the graph and answers next to the file."""
import importlib
import io
import json
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

import brain.extension
from brain.clock import HOUR
from brain.data import Store, iso_hour
from brain.extension import Extension
from brain.formula import parse
from brain.graph import MetricGraph
from brain.incidents import Book
from brain.metric_spec import SpecError, spec_from_dict
from brain.monitor import Config, Monitor, watch_list
from brain.run import live
from brain.run import main as run_main
from sim.run import backfill

ROOT = Path(__file__).resolve().parents[1]
GRAIN = "ts, country, platform, category, channel"


MAIN_CONSOLE_JS = ROOT / "ui/static/v3/app.js"          # the main console, served at /
CLASSIC_CONSOLE = ROOT / "ui/static/index.html"          # the classic console, served at /classic


def _posted(path: Path, var: str) -> str:
    text = path.read_text(encoding="utf-8")
    return re.search(rf"var {var} = '([^']+)';", text).group(1).replace("\\n", "\n")


def page_yaml(var: str = "y") -> str:
    """The definition the main console's "Add refund_rate" button posts (var y), or its "Add a duplicate metric"
    button (var dup), taken from the console's own script, so these tests check what the demo sends."""
    return _posted(MAIN_CONSOLE_JS, var)


def test_the_classic_console_posts_the_same_definitions_as_the_main_one():
    for var in ("y", "dup"):
        assert _posted(CLASSIC_CONSOLE, var) == page_yaml(var), var


def with_formula(formula: str = "refunds / orders") -> str:
    """The page's refund_rate with a formula line: the page posts the query only, so the agent finds the formula."""
    return page_yaml() + f"formula: {formula}\n"


def definition(name: str, value_sql: str | None = None, where: str = "", **fields) -> str:
    lines = [f"name: {name}"] + [f"{k}: {v}" for k, v in fields.items()]
    if value_sql:
        lines += ["sql: >", f"  SELECT {GRAIN}, {value_sql} AS value", f"  FROM facts_hourly {where} GROUP BY {GRAIN}"]
    return "\n".join(lines) + "\n"


def drop(folder: Path, stem: str, text: str, crlf: bool = True) -> Path:
    """Put a definition into the folder the way the console does on Windows: CRLF line ends."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{stem}.yaml"
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))
    return path


def look(ext: Extension, times: int = 2) -> list[dict]:
    """A file is taken once it is the same on two looks in a row."""
    out = []
    for _ in range(times):
        out += ext.poll()
    return out


def answer(ext: Extension, stem: str) -> dict:
    return json.loads((ext.incoming / f"{stem}.result.json").read_text(encoding="utf-8"))


def by_check(result: dict) -> dict:
    return {c["check"]: c for c in result["checks"]}


def tree_metrics(tree: dict) -> list[str]:
    return [tree["metric"]] + [m for c in tree["children"] for m in tree_metrics(c)]


@pytest.fixture(scope="module")
def market(tmp_path_factory):
    return backfill(str(tmp_path_factory.mktemp("extension") / "market.db"), 240)   # 10 days, seed 42


@pytest.fixture(scope="module")
def counts(market):
    con = sqlite3.connect(market)
    try:
        rows, with_orders = con.execute("SELECT COUNT(*), SUM(orders > 0) FROM facts_hourly").fetchone()
    finally:
        con.close()
    return {"rows": rows, "with_orders": with_orders}


@pytest.fixture
def ext(market, tmp_path):
    store = Store(market)
    yield Extension(MetricGraph.load(), store, tmp_path / "incoming", graph_out=tmp_path / "graph.json", quiet=True)
    store.conn.close()


# ------------------------------------------------------------------------------- the demo's definition
def test_the_pages_own_definition_with_crlf_is_accepted_and_validated(ext, counts):
    text = page_yaml()
    path = drop(ext.incoming, "refund_rate", text)
    assert b"\r\n" in path.read_bytes() and "refund_rate" in text
    [r] = look(ext)
    assert r == answer(ext, "refund_rate")
    assert r["name"] == "refund_rate" and r["status"] == "validated", r
    assert {"name", "status", "placed", "links_proposed", "checks", "message"} <= set(r)
    assert isinstance(r["links_proposed"], list) and r["links_proposed"]
    assert all(set(c) == {"check", "status", "detail"} for c in r["checks"])
    assert all(c["status"] in ("pass", "skip") for c in r["checks"]), r["checks"]
    for sentence in (r["placed"], r["message"]):
        assert sentence and "\n" not in sentence
    assert r["message"].startswith("refund_rate")
    # the page posts the query only: the agent finds the formula itself, and says so in the message
    assert "formula:" not in text and "sql:" in text
    assert r["formula"] == "refunds / orders" and r["formula_source"] == "inferred"
    assert re.search(r"formula refunds / orders found from the SQL, the only one of \d+ candidates", r["message"]), \
        r["message"]
    checks = by_check(r)
    # the SQL was compared with the formula on the rows that have a value, and the number is in the answer
    assert checks["definition"]["status"] == "pass" and str(counts["with_orders"]) in checks["definition"]["detail"]
    assert checks["rows"]["status"] == "pass" and str(counts["rows"]) in checks["rows"]["detail"]
    assert checks["rollup"]["status"] == "pass" and re.search(r"\d", checks["rollup"]["detail"])
    assert checks["alias"]["status"] == "pass"
    spec = ext.graph.specs["refund_rate"]
    assert spec.origin == "contributed" and ext.graph.status["refund_rate"] == "validated"


def test_it_finds_its_place_as_a_factor_of_an_existing_metric(ext):
    drop(ext.incoming, "refund_rate", page_yaml())
    [r] = look(ext)
    identities = [l.get("identity") for l in r["links_proposed"]]
    assert "refunds = orders * refund_rate" in identities
    g = ext.graph
    assert {e.dst for e in g.out_edges("refunds", "formula")} == {"orders", "refund_rate"}
    assert {e.dst for e in g.out_edges("refund_rate", "definition")} == {"refunds", "orders"}
    assert {e.attrs["dimension"] for e in g.out_edges("refund_rate", "breakdown")} == \
        {"country", "platform", "category", "channel"}
    assert "freshness:orders" in {n.id for n in g.context_of("refund_rate")}
    assert "refunds" in r["placed"] and "contribution" in r["placed"]      # it says the outcome tree does not reach it
    assert g.top_outcomes() == ["contribution"]
    assert "sessions" in {c for c, _ in g.formula_children("orders")}      # the core decomposition is untouched


@pytest.mark.parametrize("name, value_sql, kind, names", [
    ("refund_rate", "SUM(refunds) * 1.0 / NULLIF(SUM(orders), 0)", "ratio", ["refunds", "orders"]),
    ("platform_revenue", "SUM(marketplace_fees) + SUM(ad_spend)", "sum", ["ad_spend", "marketplace_fees"]),
    ("failed_payments", "SUM(payment_attempts) - SUM(payment_successes)", "sum", ["payment_attempts", "payment_successes"]),
    ("view_volume", "SUM(sessions) * 1.0 * SUM(product_views)", "product", ["product_views", "sessions"]),
])
def test_a_definition_with_sql_only_gets_its_formula_found(ext, name, value_sql, kind, names):
    drop(ext.incoming, name, definition(name, value_sql, unit="ratio"))
    [r] = look(ext)
    assert r["status"] in ("validated", "tentative"), r
    assert r["formula_source"] == "inferred"
    found = parse(r["formula"])
    assert found.kind == kind and sorted(found.names) == sorted(names)
    if name == "refund_rate":
        assert r["formula"] == "refunds / orders" and r["status"] == "validated"
    if name == "failed_payments":
        assert dict((n, s) for s, n in found.terms()) == {"payment_attempts": 1, "payment_successes": -1}
    detail = by_check(r)["formula"]["detail"]
    assert re.search(r"\d+ candidates", detail), detail
    assert ext.graph.specs[name].formula.text == r["formula"]


def test_an_alias_of_an_existing_metric_is_reported_and_not_added(ext):
    drop(ext.incoming, "basket_value", definition("basket_value", formula="gmv / orders", unit="eur"))
    drop(ext.incoming, "money_per_order", definition("money_per_order", "SUM(gmv) / NULLIF(SUM(orders), 0)"))
    results = {r["name"]: r for r in look(ext)}
    for name in ("basket_value", "money_per_order"):
        r = results[name]
        assert r["alias_of"] == "aov" and r["status"] == "rejected"
        assert "alias" in r["message"] and "aov" in r["message"]
        assert by_check(r)["alias"]["status"] == "fail"
        assert name not in ext.graph.specs


def test_the_pages_duplicate_metric_is_refused_as_an_alias_with_the_reason(ext):
    """The console's second button: a query that is payment_success_rate under another name. The agent finds its
    formula, sees that it is the same definition as an existing metric, and says so instead of adding a node."""
    text = page_yaml("dup")
    assert "formula:" not in text and "sql:" in text
    name = re.search(r"^name: (\w+)$", text, flags=re.M).group(1)
    drop(ext.incoming, name, text)
    [r] = look(ext)
    assert r["status"] == "rejected" and r["alias_of"] == "payment_success_rate", r
    assert r["message"].startswith(f"{name} rejected: it is an alias of payment_success_rate"), r["message"]
    assert "payment_successes / payment_attempts" in r["message"] and "no new node was added" in r["message"]
    checks = by_check(r)
    assert checks["alias"]["status"] == "fail" and checks["formula"]["status"] == "pass"
    assert "found payment_successes / payment_attempts" in checks["formula"]["detail"]
    assert [c["check"] for c in r["checks"]][:3] == ["read", "sql", "formula"]
    assert name not in ext.graph.specs and r["placed"].startswith("not placed")


# ------------------------------------------------------------------------------------------- rejections
REJECTED = {
    "bad_name": ("name: Refund Rate\nformula: refunds / orders\n", "bad metric name"),
    "core_name": ("name: orders\nformula: refunds / sessions\n", "already exists"),
    "unknown_input": ("name: return_rate\nformula: returns / orders\n", "unknown metrics"),
    "no_definition": ("name: mystery\ndescription: nobody said how to compute it\n", "it needs a formula or a SQL query"),
    "hidden_table": ("name: peek\nsql: SELECT injected_at AS ts, 1.0 AS value FROM ground_truth\n", "contract tables"),
    "broken_sql": ("name: typo_rate\nsql: SELECT ts, SUM(refundz) AS value FROM facts_hourly GROUP BY ts\n", "SQL failed"),
    "wrong_formula": (definition("refund_share", "SUM(refunds) * 1.0 / NULLIF(SUM(orders), 0)",
                                 formula="refunds / sessions"), "disagree"),
    "no_formula": (definition("odd_number", "SUM(refunds * refunds) + 0.5"), "no formula"),
    "wrong_grain": ("name: by_country\nformula: refunds / orders\ndimensions: [country, platform]\n"
                    "sql: SELECT ts, country, SUM(refunds) * 1.0 / SUM(orders) AS value FROM facts_hourly "
                    "GROUP BY ts, country\n", "declares dimensions"),
    "not_a_metric": ("- refunds\n- orders\n", "mapping"),
    "several": ("metrics:\n  - name: one\n    formula: refunds / orders\n", "one metric per file"),
}


def test_each_rejection_has_its_own_message_and_the_loop_goes_on(ext):
    for stem, (text, _) in REJECTED.items():
        drop(ext.incoming, stem, text)
    drop(ext.incoming, "refund_rate", page_yaml())
    results = look(ext)
    assert len(results) == len(REJECTED) + 1
    for stem, (_, fragment) in REJECTED.items():
        r = answer(ext, stem)
        assert r["status"] == "rejected", (stem, r)
        assert fragment in r["message"], (stem, r["message"])
        assert "\n" not in r["message"] and r["placed"] and r["links_proposed"] == []
        assert any(c["status"] == "fail" for c in r["checks"]), (stem, r["checks"])
    messages = [answer(ext, stem)["message"] for stem in REJECTED]
    assert len(set(messages)) == len(messages)
    assert answer(ext, "core_name")["message"].startswith("orders rejected: the name already exists in the graph (a core")
    assert "refundz" in answer(ext, "broken_sql")["message"]
    assert "returns" in answer(ext, "unknown_input")["message"]
    # the bad files did not stop the good one, and none of them reached the graph
    assert answer(ext, "refund_rate")["status"] == "validated"
    contributed = [m for m, s in ext.graph.specs.items() if s.origin != "core"]
    assert contributed == ["refund_rate"]


def test_a_query_that_never_ends_is_stopped_and_rejected(market, tmp_path):
    endless = "FROM facts_hourly a, facts_hourly b, facts_hourly c"         # 43,200 ** 3 rows to walk through
    store = Store(market)
    with pytest.raises(TimeoutError):
        store.read_sql(f"SELECT COUNT(*) AS n {endless}", timeout=0.2)
    assert len(store.read_sql("SELECT ts FROM facts_hourly LIMIT 3", timeout=5)) == 3     # the limit is per query
    with pytest.raises(OverflowError):
        store.read_sql("SELECT ts FROM facts_hourly", max_rows=1000)
    assert len(store.read_sql("SELECT ts FROM facts_hourly LIMIT 7", max_rows=1000)) == 7
    ext = Extension(MetricGraph.load(), store, tmp_path / "incoming", quiet=True, sql_timeout=0.2)
    drop(ext.incoming, "endless", f"name: endless\nsql: SELECT a.ts AS ts, COUNT(*) AS value {endless} GROUP BY a.ts\n")
    [r] = look(ext)
    assert r["status"] == "rejected" and "longer than" in r["message"]
    store.conn.close()


# ------------------------------------------------------------------------ files that are not ready yet
def test_an_empty_or_half_written_file_is_tried_again_never_rejected(ext):
    text = page_yaml().replace("\n", "\r\n")
    path = ext.incoming / "refund_rate.yaml"
    ext.incoming.mkdir(parents=True)
    path.write_bytes(b"")                                   # the console has opened the file and written nothing yet
    assert look(ext, 3) == []
    for cut in (3, text.index("description") + 5, text.rindex("category, channel]")):
        path.write_bytes(text[:cut].encode())               # cut inside a key or inside a list: not a definition yet
        assert look(ext, 3) == []
    assert not (ext.incoming / "refund_rate.result.json").exists()
    # cut at a line end after the first lines of the query: readable YAML with half a query. One look sees it,
    # the next look sees the whole file; the half is never judged
    cut = text.index("SUM(refunds)")
    path.write_bytes(text[:text.rindex("\r\n", 0, cut) + 2].encode())
    assert ext.poll() == []
    path.write_bytes(text.encode())
    results = look(ext, 3)
    assert [r["status"] for r in results] == ["validated"]
    assert "refund_rate" in ext.graph.specs


def test_a_second_identical_click_does_nothing_and_a_changed_file_is_taken_again(ext):
    path = drop(ext.incoming, "refund_rate", page_yaml())
    assert len(look(ext)) == 1
    result_path, graph_path = ext.incoming / "refund_rate.result.json", Path(ext.graph_out)
    before = (result_path.stat().st_mtime_ns, graph_path.stat().st_mtime_ns, result_path.read_bytes())
    path.write_bytes(b"")                                                  # the second click: truncated, ...
    assert ext.poll() == []
    drop(ext.incoming, "refund_rate", page_yaml())                         # ... then the same text again
    assert look(ext, 4) == []
    assert (result_path.stat().st_mtime_ns, graph_path.stat().st_mtime_ns, result_path.read_bytes()) == before

    drop(ext.incoming, "refund_rate", page_yaml().replace("Share of orders refunded", "Refunded orders per order"))
    assert ext.poll() == []
    assert not result_path.exists()            # the old answer is not shown for the new text
    [r] = look(ext)
    assert r["status"] == "validated" and ext.graph.specs["refund_rate"].description == "Refunded orders per order"


def test_a_file_changed_into_a_bad_definition_withdraws_its_metric(ext):
    drop(ext.incoming, "refund_rate", page_yaml())
    look(ext)
    assert "refund_rate" in ext.graph.specs
    drop(ext.incoming, "refund_rate", with_formula("refunds / sessions"))
    [r] = look(ext)
    assert r["status"] == "rejected" and "disagree" in r["message"]
    assert "refund_rate" not in ext.graph.specs and ext.graph.out_edges("refunds", "formula") == []
    exported = json.loads(Path(ext.graph_out).read_text(encoding="utf-8"))
    assert "refund_rate" not in {n["id"] for n in exported["nodes"]}
    assert [t["metric"] for t in exported["trees"]] == ["contribution"]


def test_a_metric_built_on_a_withdrawn_one_is_judged_again(ext):
    drop(ext.incoming, "refund_rate", page_yaml())
    drop(ext.incoming, "refunds_per_view", definition("refunds_per_view", formula="refund_rate * conversion_rate"))
    look(ext, 4)
    assert answer(ext, "refunds_per_view")["status"] == "validated"
    drop(ext.incoming, "refund_rate", "name: refund_rate\ndescription: the formula was taken out\n")
    look(ext, 6)
    assert answer(ext, "refund_rate")["status"] == "rejected"
    r = answer(ext, "refunds_per_view")
    assert r["status"] == "rejected" and "unknown metrics ['refund_rate']" in r["message"]
    assert [m for m, s in ext.graph.specs.items() if s.origin != "core"] == []


def test_an_error_of_ours_is_tried_again_then_answered_and_the_loop_goes_on(ext, monkeypatch):
    calls = []

    def broken(path, raw, *more):
        calls.append(path.name)
        raise RuntimeError("database is locked")

    monkeypatch.setattr(ext, "_judge", broken)
    drop(ext.incoming, "refund_rate", page_yaml())
    assert look(ext, 3) == [] and len(calls) == 2            # the second and the third look tried it
    [r] = look(ext, 1)
    assert r["status"] == "rejected" and "could not be checked" in r["message"] and "database is locked" in r["message"]
    assert look(ext, 3) == [] and len(calls) == 3            # answered: not tried on every poll for ever


def test_two_files_cannot_define_the_same_name(ext):
    drop(ext.incoming, "refund_rate", page_yaml())
    drop(ext.incoming, "zz_copy", page_yaml().replace("Share of orders refunded", "The same name again"))
    look(ext)
    assert answer(ext, "refund_rate")["status"] == "validated"
    r = answer(ext, "zz_copy")
    assert r["status"] == "rejected" and "already exists" in r["message"] and "refund_rate.yaml" in r["message"]


# -------------------------------------------------------------------- what the SQL really defines
def test_a_query_that_returns_part_of_the_rows_is_tentative(ext, counts):
    drop(ext.incoming, "refund_rate_de", definition("refund_rate_de", "SUM(refunds) * 1.0 / NULLIF(SUM(orders), 0)",
                                                    where="WHERE country = 'DE'", formula="refunds / orders"))
    [r] = look(ext)
    rows = by_check(r)["rows"]
    assert r["status"] == "tentative" and rows["status"] == "warn"
    assert str(counts["rows"]) in rows["detail"] and "part" in r["message"]
    assert ext.graph.status["refund_rate_de"] == "tentative"
    # a tentative metric does not change how another metric decomposes: the identity stays a proposal
    assert ext.graph.out_edges("refunds", "formula") == []
    assert [l["status"] for l in r["links_proposed"] if l.get("identity")] == ["proposed"]
    exported = json.loads(Path(ext.graph_out).read_text(encoding="utf-8"))
    assert [n["status"] for n in exported["nodes"] if n["id"] == "refund_rate_de"] == ["tentative"]


def test_a_difference_goes_under_its_whole_and_the_remainder_reading_is_only_kept(ext):
    drop(ext.incoming, "failed_payments", definition("failed_payments", formula="payment_attempts - payment_successes",
                                                     unit="count"))
    [r] = look(ext)
    assert r["status"] == "validated", r
    by_identity = {l["identity"]: l for l in r["links_proposed"] if l.get("identity") and l["target"] == "failed_payments"}
    assert by_identity["payment_attempts = payment_successes + failed_payments"]["applied"] is True
    kept = by_identity["payment_successes = payment_attempts - failed_payments"]
    assert kept["status"] == "alternative" and "what is left" in kept["reason"]
    assert {e.dst for e in ext.graph.out_edges("payment_attempts", "formula")} == {"payment_successes", "failed_payments"}
    assert ext.graph.out_edges("payment_successes", "formula") == []
    assert by_check(r)["rollup"]["status"] == "pass" and "added up" in by_check(r)["rollup"]["detail"]


def test_an_identity_with_the_outcome_is_kept_as_an_alternative(ext):
    drop(ext.incoming, "platform_revenue", definition("platform_revenue", formula="marketplace_fees + ad_spend", unit="eur"))
    [r] = look(ext)
    assert r["status"] == "validated"
    [link] = [l for l in r["links_proposed"] if l.get("identity") == "contribution = platform_revenue - discounts"]
    assert link["status"] == "alternative" and "already decomposes" in link["reason"]
    assert ext.graph.specs["contribution"].formula.text == "marketplace_fees + ad_spend - discounts"
    assert {e.dst for e in ext.graph.out_edges("platform_revenue", "formula")} == {"marketplace_fees", "ad_spend"}
    exported = json.loads(Path(ext.graph_out).read_text(encoding="utf-8"))
    assert [t["metric"] for t in exported["trees"]] == ["contribution", "platform_revenue"]
    assert all(c["repeated"] for c in exported["trees"][1]["children"])


def test_an_ambiguous_place_is_said_so(market, tmp_path):
    """Two metrics on one column are the same thing twice: a rate of refunds fits under either reading of the base."""
    core = [spec_from_dict(raw) for raw in (
        {"name": "orders", "column": "orders", "unit": "count", "source": "orders"},
        {"name": "purchases", "column": "orders", "unit": "count", "source": "orders"},
        {"name": "refunds", "column": "refunds", "unit": "count", "source": "orders"})]
    store = Store(market)
    ext = Extension(MetricGraph.from_specs(core), store, tmp_path / "incoming", graph_out=tmp_path / "graph.json", quiet=True)
    drop(ext.incoming, "refund_rate", with_formula())   # the query alone would fit refunds / purchases too
    [r] = look(ext)
    store.conn.close()
    assert r["status"] == "tentative" and "ambiguous" in r["message"] and "ambiguous" in r["placed"]
    fitting = {l["identity"]: l["status"] for l in r["links_proposed"] if l.get("identity")}
    assert fitting == {"refunds = orders * refund_rate": "ambiguous", "refunds = purchases * refund_rate": "ambiguous"}
    assert ext.graph.out_edges("refunds", "formula") == []                 # neither is applied
    assert by_check(r)["placement"]["status"] == "warn"
    assert ext.graph.status["refund_rate"] == "tentative"


def test_a_place_inside_the_outcome_tree_is_proposed_not_applied(ext):
    """sessions is a leaf of the outcome tree: giving it a decomposition would change how the outcome is explained."""
    drop(ext.incoming, "sessions_per_search", definition("sessions_per_search", formula="sessions / searches"))
    [r] = look(ext)
    assert r["status"] == "validated"
    [link] = [l for l in r["links_proposed"] if l.get("identity") == "sessions = searches * sessions_per_search"]
    assert link["status"] == "proposed"
    assert ext.graph.out_edges("sessions", "formula") == []
    assert "proposed" in r["placed"]
    # it is still drawn: a tree of its own under the outcome's, with what it is defined from
    exported = json.loads(Path(ext.graph_out).read_text(encoding="utf-8"))
    assert [t["metric"] for t in exported["trees"]] == ["contribution", "sessions_per_search"]
    assert [(c["metric"], c["role"], c["repeated"]) for c in exported["trees"][1]["children"]] == \
        [("sessions", "numerator", True), ("searches", "denominator", False)]
    assert exported["trees"][0] == MetricGraph.load().to_dict()["trees"][0]      # the outcome's tree is as it was


# -------------------------------------------------------------------------------------- the export
def test_the_export_keeps_the_outcome_first_and_carries_the_new_node(ext):
    drop(ext.incoming, "refund_rate", page_yaml())
    look(ext)
    path = Path(ext.graph_out)
    exported = json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda name: pytest.fail(name))
    assert exported["top_outcomes"] == ["contribution"]
    assert exported["trees"][0]["metric"] == "contribution" and len(exported["trees"]) == 2
    assert "refund_rate" not in tree_metrics(exported["trees"][0])
    second = exported["trees"][1]
    assert "refund_rate" in tree_metrics(second)
    assert len(tree_metrics(second)) <= 4            # what the outcome tree already shows is not drawn again
    [node] = [n for n in exported["nodes"] if n["id"] == "refund_rate"]
    assert node["status"] == "validated" and node["origin"] == "contributed" and node["kind"] == "metric"
    assert node["formula"] == "refunds / orders" and node["domain"] is None
    core = [n for n in exported["nodes"] if n["kind"] == "metric" and n["id"] != "refund_rate"]
    assert core and all(n["origin"] == "core" and n["status"] == "validated" for n in core)
    ids = {n["id"] for n in exported["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in exported["edges"])
    assert {e["target"] for e in exported["edges"] if e["source"] == "refund_rate" and e["kind"] == "breakdown"} == \
        {f"refund_rate@{d}" for d in exported["dimensions"]}
    assert not list(path.parent.glob("*.tmp")) and not list(ext.incoming.glob("*.tmp"))
    assert b"\r" not in path.read_bytes()


def test_a_rejected_definition_does_not_rewrite_the_export(ext):
    drop(ext.incoming, "bad", "name: orders\nformula: refunds / sessions\n")
    look(ext)
    assert answer(ext, "bad")["status"] == "rejected"
    assert not Path(ext.graph_out).exists()


def test_a_domain_given_with_a_metric_reaches_its_node(ext, graph):
    assert spec_from_dict({"name": "a", "column": "a", "domain": "orders"}).domain == "orders"
    with pytest.raises(SpecError, match="domain"):
        spec_from_dict({"name": "a", "column": "a", "domain": ["orders"]})
    core = [n for n in graph.to_dict()["nodes"] if n["kind"] == "metric"]
    assert core and all(n["domain"] is None for n in core)             # not filled in for the core metrics
    drop(ext.incoming, "refund_rate", page_yaml() + "domain: orders\n")
    [r] = look(ext)
    assert r["status"] == "validated"
    exported = json.loads(Path(ext.graph_out).read_text(encoding="utf-8"))
    assert [n["domain"] for n in exported["nodes"] if n["id"] == "refund_rate"] == ["orders"]


# ------------------------------------------------------------------------------------- the console
@pytest.fixture
def console(market, tmp_path, monkeypatch):
    """The console the way a second process sees it: its folders come from the environment when it is imported."""
    from fastapi.testclient import TestClient
    for key, value in (("MARKET_DB", market), ("INCOMING_METRICS_DIR", tmp_path / "incoming"),
                       ("GRAPH_JSON", tmp_path / "graph.json"), ("INCIDENTS_DIR", tmp_path / "incidents")):
        monkeypatch.setenv(key, str(value))
    import ui.app
    module = importlib.reload(ui.app)
    try:
        yield TestClient(module.app)
    finally:
        monkeypatch.undo()
        importlib.reload(ui.app)          # other tests get the console with its default folders back


def test_the_console_returns_the_result_file_and_the_new_graph(console, market, tmp_path):
    assert console.get("/api/metric/refund_rate").status_code == 404
    posted = console.post("/api/metric", json={"yaml_text": page_yaml()})
    assert posted.status_code == 200 and posted.json()["name"] == "refund_rate"
    store = Store(market)
    ext = Extension(MetricGraph.load(), store, tmp_path / "incoming", graph_out=tmp_path / "graph.json", quiet=True)
    [r] = look(ext)
    store.conn.close()
    got = console.get("/api/metric/refund_rate")
    assert got.status_code == 200 and got.json() == r == answer(ext, "refund_rate")
    assert got.json()["status"] == "validated" and got.json()["message"]
    g = console.get("/api/graph").json()
    assert g["trees"][0]["metric"] == "contribution"
    assert [(n["status"], n["origin"]) for n in g["nodes"] if n["id"] == "refund_rate"] == [("validated", "contributed")]


# ------------------------------------------------------------------------------ the monitor's loop
def test_fresh_removes_old_answers_and_keeps_the_definitions(market, tmp_path):
    incoming = tmp_path / "incoming"
    drop(incoming, "refund_rate", page_yaml())
    (incoming / "refund_rate.result.json").write_text('{"status": "validated", "message": "from the last run"}')
    (incoming / "gone.result.json").write_text('{"status": "rejected", "message": "from the last run"}')
    store = Store(market)
    Extension(MetricGraph.load(), store, incoming, quiet=True)                       # not fresh: nothing is removed
    assert (incoming / "gone.result.json").exists()
    Extension(MetricGraph.load(), store, incoming, quiet=True, fresh=True)
    store.conn.close()
    assert sorted(p.name for p in incoming.iterdir()) == ["refund_rate.yaml"]


def test_live_mode_looks_at_the_incoming_folder_on_every_poll(market, tmp_path):
    incoming, graph_out = tmp_path / "incoming", tmp_path / "graph.json"
    drop(incoming, "refund_rate", page_yaml())
    (incoming / "gone.result.json").write_text('{"status": "rejected", "message": "from the last run"}')
    code = run_main(["--db", str(market), "--incidents", str(tmp_path / "incidents"), "--incoming", str(incoming),
                     "--graph-out", str(graph_out), "--fresh", "--max-ticks", "2", "--poll", "0.01", "--quiet"])
    assert code == 0
    assert not (incoming / "gone.result.json").exists() and (incoming / "refund_rate.yaml").exists()
    r = json.loads((incoming / "refund_rate.result.json").read_text(encoding="utf-8"))
    assert r["status"] == "validated"
    exported = json.loads(graph_out.read_text(encoding="utf-8"))
    assert "refund_rate" in {n["id"] for n in exported["nodes"]} and exported["trees"][0]["metric"] == "contribution"


def test_replay_does_not_touch_the_incoming_folder(market, tmp_path, monkeypatch):
    incoming = tmp_path / "incoming"
    drop(incoming, "refund_rate", page_yaml())
    monkeypatch.setenv("INCOMING_METRICS_DIR", str(incoming))
    assert run_main(["--replay", "--db", str(market), "--incidents", str(tmp_path / "incidents"), "--fresh", "--quiet",
                     "--from", "2026-11-03T00:00:00Z", "--to", "2026-11-03T02:00:00Z"]) == 0
    assert sorted(p.name for p in incoming.iterdir()) == ["refund_rate.yaml"]


class FixedMonitor(Monitor):
    """A monitor that builds its watch list once, at start, and cannot take a rate later."""
    rewatch = None


def test_an_accepted_rate_is_ready_for_the_monitor_and_the_answer_says_whether_it_is_watched(market, tmp_path):
    store, graph = Store(market), MetricGraph.load()
    monitor = FixedMonitor(graph, store, Config())
    ext = Extension(graph, store, tmp_path / "incoming", monitor=monitor, quiet=True)
    drop(ext.incoming, "refund_rate", page_yaml())
    [r] = look(ext)
    [w] = [w for w in watch_list(graph) if w.metric == "refund_rate"]         # the graph the monitor holds has it
    assert (w.numerator, w.denominator, w.sources, w.counts) == ("refunds", "orders", ("orders",), True)
    assert "registered, not watched yet" in r["message"]                      # this monitor fixes its list at start
    assert by_check(r)["monitoring"]["status"] == "skip"
    assert "refund_rate" not in {w.metric for w in monitor.watched}
    store.conn.close()


def test_the_new_rate_is_judged_from_the_next_tick(market, tmp_path):
    store, graph = Store(market), MetricGraph.load()
    monitor = Monitor(graph, store, Config())
    judged_before = monitor.tick().judged_series
    ext = Extension(graph, store, tmp_path / "incoming", monitor=monitor, quiet=True)
    drop(ext.incoming, "refund_rate", page_yaml())
    [r] = look(ext)
    assert "watched from the next tick" in r["message"] and "not watched" not in r["message"]
    assert by_check(r)["monitoring"]["status"] == "pass"
    tick = monitor.tick()
    assert tick.judged_series > judged_before
    assert monitor.expected_rate("refund_rate", {}) == pytest.approx(0.03, abs=0.01)
    assert monitor.expected_rate("payment_success_rate", {}) is not None      # the rates it had are still judged
    assert not [a for a in tick.alerts if a.metric == "refund_rate"]           # clean data: nothing to report
    # the file is changed into a bad definition: the rate leaves the graph and the watch list with it
    drop(ext.incoming, "refund_rate", with_formula("refunds / sessions"))
    [r] = look(ext)
    assert r["status"] == "rejected" and "refund_rate" not in {w.metric for w in monitor.watched}
    assert monitor.tick().judged_series == judged_before
    store.conn.close()


def test_a_fault_in_a_rate_that_arrived_at_run_time_is_raised_and_reported(tmp_path):
    """refund_rate is handed over while the monitor runs; later refunds jump in one country. The new rate is
    judged like the ones the monitor started with: one incident that names the rate and the place. The rate
    is outside the outcome tree, so the report carries no cost rather than an invented one."""
    db = backfill(str(tmp_path / "market.db"), 240)
    con = sqlite3.connect(db)
    try:
        last = pd.Timestamp(con.execute("SELECT MAX(ts) FROM facts_hourly").fetchone()[0])
        start = last - 5 * HOUR
        con.execute("UPDATE facts_hourly SET refunds = MIN(orders, refunds * 4 + 1) WHERE country = 'PL' AND ts >= ?",
                    (iso_hour(start),))
        con.commit()
    finally:
        con.close()
    store, graph = Store(db), MetricGraph.load()
    monitor = Monitor(graph, store, Config())
    book = Book(monitor, tmp_path / "incidents", fresh=True)
    ext = Extension(graph, store, tmp_path / "incoming", monitor=monitor, quiet=True)
    assert not monitor.tick(as_of=start - HOUR).alerts              # running, and quiet, before the file arrives
    drop(ext.incoming, "refund_rate", page_yaml())
    [r] = look(ext)
    assert r["status"] == "validated" and "watched from the next tick" in r["message"]
    assert not monitor.tick(as_of=start).alerts                     # the last clean hour, now with the new rate

    raised_at = None
    t = start + HOUR
    while t <= last + HOUR:
        tick = monitor.tick(as_of=t)
        assert {a.metric for a in tick.alerts} <= {"refund_rate"}   # nothing else moved
        if book.update(tick) and raised_at is None:
            raised_at = t
        t += HOUR
    assert raised_at == start + HOUR                                # on the first affected hour

    [path] = sorted((tmp_path / "incidents").glob("inc-*.json"))
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["status"] in ("open", "ongoing") and body["segment"] == {"country": "PL"}
    assert [d["metric"] for d in body["drivers"]] == ["refund_rate"]
    assert body["change"]["window"]["start"] == iso_hour(start)
    assert not body.get("impact") and "explained_share" not in body   # no path to the outcome: no cost is claimed
    assert not [h for h in body["hypotheses"] if h["status"] == "supported"]   # no release or event fits
    assert {h["kind"] for h in body["hypotheses"]} >= {"metric", "freshness"}
    assert [s["action"] for s in body["trail"]][:3] == ["watch", "compare", "drill"]
    assert body["report"].startswith("refund_rate rose") and " in PL" in body["report"]
    store.conn.close()


# ------------------------------------------------------------- what the two reviews found (C1-C3, V1-V6)
RATE_SQL = "SUM(refunds) * 1.0 / NULLIF(SUM(orders), 0)"


def contributed(ext: Extension) -> list[str]:
    return [m for m, s in ext.graph.specs.items() if s.origin != "core"]


def test_a_definition_built_on_another_file_of_the_same_look_is_judged_after_it(ext):
    """C1. After a restart every file is taken in one look, in the order of the file names."""
    drop(ext.incoming, "a_gap", definition("a_gap", formula="z_rate - aov"))
    drop(ext.incoming, "z_rate", definition("z_rate", formula="refunds / orders"))
    results = {r["name"]: r for r in look(ext)}
    assert results["z_rate"]["status"] == "validated"
    assert results["a_gap"]["status"] == "validated", results["a_gap"]["message"]
    assert contributed(ext) == ["z_rate", "a_gap"]
    assert answer(ext, "a_gap")["status"] == "validated"


def test_a_chain_of_definitions_in_the_worst_order_is_taken_in_one_look(ext):
    drop(ext.incoming, "a_third", definition("a_third", formula="b_second * add_to_cart_rate"))
    drop(ext.incoming, "b_second", definition("b_second", formula="c_first * conversion_rate"))
    drop(ext.incoming, "c_first", definition("c_first", formula="refunds / orders"))
    results = {r["name"]: r["status"] for r in look(ext)}
    assert results == {"c_first": "validated", "b_second": "validated", "a_third": "validated"}
    assert contributed(ext) == ["c_first", "b_second", "a_third"]


def test_definitions_that_need_each_other_get_their_answers_and_nothing_waits_for_ever(ext):
    drop(ext.incoming, "a_one", definition("a_one", formula="b_two + orders"))
    drop(ext.incoming, "b_two", definition("b_two", formula="a_one + orders"))
    drop(ext.incoming, "c_broken", "name: c_broken\nformula: [refunds / orders\n")     # never readable
    results = look(ext)
    assert sorted(r["name"] for r in results) == ["a_one", "b_two"]
    assert all(r["status"] == "rejected" and "unknown metrics" in r["message"] for r in results)
    assert contributed(ext) == []


def test_a_definition_waits_for_a_file_that_is_still_being_written(ext):
    drop(ext.incoming, "a_gap", definition("a_gap", formula="z_rate - aov"))
    assert ext.poll() == []
    drop(ext.incoming, "z_rate", definition("z_rate", formula="refunds / orders"))
    assert ext.poll() == []            # a_gap is settled, z_rate was seen once: no answer yet, and not a rejection
    assert not (ext.incoming / "a_gap.result.json").exists()
    results = {r["name"]: r["status"] for r in look(ext, 2)}
    assert results == {"z_rate": "validated", "a_gap": "validated"}


@pytest.mark.parametrize("tolerance", ["1", "0.05", "-1", "0", "loose", "[1, 2]"])
def test_a_definition_may_not_loosen_its_own_tolerance(ext, tolerance, capsys):
    """C2, V1. With tolerance 1 any two values of the same sign are "equal"."""
    drop(ext.incoming, "wrong_tol", definition("wrong_tol", RATE_SQL, formula="discounts / orders", tolerance=tolerance))
    [r] = look(ext)
    assert r["status"] == "rejected" and "tolerance" in r["message"], r["message"]
    assert by_check(r)["read"]["status"] == "fail" and list(by_check(r)) == ["read"]
    assert contributed(ext) == []
    assert "Traceback" not in capsys.readouterr().err


def test_a_tolerance_inside_the_limit_is_kept(ext):
    drop(ext.incoming, "refund_rate", page_yaml() + "tolerance: 1e-9\n")
    [r] = look(ext)
    assert r["status"] == "validated" and ext.graph.specs["refund_rate"].tolerance == 1e-9
    assert "tolerance 1e-09" in by_check(r)["definition"]["detail"]


def test_a_field_of_the_wrong_kind_is_answered_at_once(ext, capsys):
    drop(ext.incoming, "odd_bounds", "name: odd_bounds\nformula: refunds / orders\nbounds: [low, high]\n")
    [r] = look(ext)
    assert r["status"] == "rejected" and by_check(r)["read"]["status"] == "fail" and "low" in r["message"]
    assert "Traceback" not in capsys.readouterr().err


def test_poll_keeps_its_promise_when_the_message_cannot_be_printed(ext, monkeypatch):
    """C3. A console in cp1251 cannot print every character a definition may hold."""
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1251")
    monkeypatch.setattr(sys, "stdout", out)
    ext.quiet = False
    drop(ext.incoming, "cafe", "name: café_rate\nformula: refunds / orders\n")
    [r] = look(ext)                                   # it did not raise, and the answer is returned
    assert r["status"] == "rejected" and "bad metric name" in r["message"]
    out.flush()
    assert b"bad metric name" in out.buffer.getvalue()


def test_poll_does_not_raise_when_saying_fails(ext, monkeypatch):
    def cannot(text):
        raise OSError("the console is gone")

    monkeypatch.setattr(ext, "_say", cannot)
    drop(ext.incoming, "refund_rate", page_yaml())
    [r] = look(ext)
    assert r["status"] == "validated" and answer(ext, "refund_rate") == r


def test_a_failing_look_at_the_incoming_folder_does_not_cost_the_monitor_its_tick(market, tmp_path, capsys):
    class Stop(BaseException):
        pass

    class Broken:
        calls = 0

        def poll(self):
            self.calls += 1
            if self.calls > 40:
                raise Stop
            raise RuntimeError("the incoming folder cannot be read")

    store = Store(market)
    monitor = Monitor(MetricGraph.load(), store, Config())
    broken = Broken()
    try:
        live(monitor, Book(monitor, tmp_path / "incidents", fresh=True), poll=0.0, max_ticks=2, quiet=True, extension=broken)
    except Stop:
        pytest.fail("the monitor did not tick once in 40 polls: every failed look at the folder cost it its tick")
    finally:
        store.conn.close()
    captured = capsys.readouterr()
    assert "the incoming folder cannot be read" in captured.err and "tick failed" not in captured.err


@pytest.mark.parametrize("name, value_sql, formula", [
    ("avg_fine", "AVG(refunds * 1.0 / NULLIF(orders, 0))", None),
    ("avg_given", "AVG(refunds * 1.0 / NULLIF(orders, 0))", "refunds / orders"),
    ("max_ratio", "MAX(refunds * 1.0 / NULLIF(orders, 0))", "refunds / orders"),
    ("avg_net", "AVG(gmv) - AVG(discounts)", None),
    ("per_row", "SUM(refunds) * 1.0 / COUNT(*) / NULLIF(SUM(orders), 0)", "refunds / orders"),
])
def test_a_query_that_is_not_a_function_of_sums_is_tentative(ext, name, value_sql, formula):
    """V2. At the finest grain a group is one fact row, so an average or a maximum of rows equals the formula
    there. The query is run again on a copy in which every fact row is two rows."""
    drop(ext.incoming, name, definition(name, value_sql, **({"formula": formula} if formula else {})))
    [r] = look(ext)
    split = by_check(r)["split"]
    assert r["status"] == "tentative" and split["status"] == "warn", r
    changed, compared = map(int, re.search(r"(\d+) of (\d+) values", split["detail"]).groups())
    assert 0 < changed <= compared
    assert "not a function of sums" in r["message"]
    assert ext.graph.status[name] == "tentative"
    assert ext.graph.out_edges("refunds", "formula") == []      # a tentative metric decomposes nothing


def test_a_query_of_sums_passes_the_split_rerun_and_the_market_is_not_written(ext, market):
    before = Path(market).stat().st_mtime_ns, Path(market).stat().st_size
    drop(ext.incoming, "refund_rate", page_yaml())
    [r] = look(ext)
    split = by_check(r)["split"]
    assert r["status"] == "validated" and split["status"] == "pass"
    rows, values = map(int, re.search(r"(\d+) fact rows.* none of (\d+) values", split["detail"]).groups())
    assert rows == 24 * 180 and 0 < values <= rows
    assert (Path(market).stat().st_mtime_ns, Path(market).stat().st_size) == before
    drop(ext.incoming, "net_gmv", definition("net_gmv", formula="gmv - discounts"))
    [r] = look(ext)
    assert by_check(r)["split"]["status"] == "skip"             # no query to run again


def test_the_split_rerun_runs_on_a_private_copy_inside_the_contract(market):
    store = Store(market)
    try:
        whole, halves, rows = store.rerun_on_split_rows(
            "SELECT COUNT(*) AS n, SUM(orders) AS orders, SUM(gmv) AS gmv, MAX(orders) AS top FROM facts_hourly", hours=6)
        assert rows == 6 * 180 and whole["n"][0] == rows and halves["n"][0] == 2 * rows
        assert halves["orders"][0] == whole["orders"][0]                    # counts are split into whole numbers
        assert halves["gmv"][0] == pytest.approx(whole["gmv"][0], rel=1e-12)
        assert halves["top"][0] < whole["top"][0]
        assert whole["orders"][0] == store.sums(["orders"], store.time_range()[1] - 5 * brain.extension.HOUR)["orders"][0]
        again = store.rerun_on_split_rows("SELECT SUM(orders) AS orders, MAX(orders) AS top FROM facts_hourly", hours=6)
        assert again[1]["top"][0] == halves["top"][0]                       # the same split every time
        with pytest.raises(PermissionError):
            store.rerun_on_split_rows("SELECT name FROM sqlite_master")
        with pytest.raises(Exception, match="no such table"):
            store.rerun_on_split_rows("SELECT * FROM ground_truth")
        with pytest.raises(TimeoutError):
            store.rerun_on_split_rows("SELECT COUNT(*) AS n FROM facts_hourly a, facts_hourly b, facts_hourly c", timeout=0.2)
        assert len(store.read_sql("SELECT ts FROM facts_hourly LIMIT 3")) == 3   # the market's connection is as it was
    finally:
        store.conn.close()


def test_an_accepted_query_is_not_run_again_without_limits(ext, monkeypatch):
    """V3. The monitor's loop waits while a contributed query runs."""
    runs, real = [], ext.store.read_sql

    def recorded(sql, params=(), timeout=None, max_rows=None):
        if "refunds" in sql:
            runs.append((timeout, max_rows))
        return real(sql, params, timeout=timeout, max_rows=max_rows)

    monkeypatch.setattr(ext.store, "read_sql", recorded)
    drop(ext.incoming, "refund_rate", page_yaml())
    [r] = look(ext)
    assert r["status"] == "validated"
    assert runs and all(timeout and max_rows for timeout, max_rows in runs), runs
    assert brain.extension.SQL_TIMEOUT <= 3
    count = len(runs)
    drop(ext.incoming, "net_gmv", definition("net_gmv", formula="gmv - discounts"))
    [r] = look(ext)
    assert r["status"] == "validated" and len(runs) == count     # another file does not run this query again
    assert ext.graph.specs["refund_rate"].sql and ext.graph.status["refund_rate"] == "validated"


@pytest.fixture
def parses(monkeypatch):
    """How many times the YAML parser was called."""
    calls, real = [], brain.extension.yaml.safe_load

    def counted(stream):
        calls.append(len(stream))
        return real(stream)

    monkeypatch.setattr(brain.extension.yaml, "safe_load", counted)
    return calls


def test_an_unreadable_file_is_parsed_once_per_content(ext, parses):
    """V4."""
    path = drop(ext.incoming, "broken", "name: broken\nformula: refunds / orders\nsystems: [a, a, a\n")
    assert look(ext, 6) == [] and len(parses) == 1
    assert not (ext.incoming / "broken.result.json").exists()            # still never rejected
    path.write_bytes(b"name: broken\nformula: refunds / orders\nsystems: [a, a\n")
    assert look(ext, 6) == [] and len(parses) == 2                       # a changed text is read again
    drop(ext.incoming, "broken", definition("broken_no_more", formula="refunds / orders"))
    assert [r["status"] for r in look(ext)] == ["validated"]


def test_a_file_too_large_to_be_a_definition_is_answered_once_and_never_parsed(ext, parses):
    drop(ext.incoming, "refund_rate", page_yaml())
    assert [r["status"] for r in look(ext)] == ["validated"] and len(parses) == 1
    drop(ext.incoming, "refund_rate", page_yaml() + "description: " + "a" * 70_000 + "\n")
    [r] = look(ext, 4)
    assert r["status"] == "rejected" and "64 KB" in r["message"] and re.search(r"7\d\d\d\d bytes", r["message"])
    assert answer(ext, "refund_rate") == r and len(parses) == 1
    assert contributed(ext) == []                                        # what the file defined before is withdrawn
    assert look(ext, 3) == []


def test_a_deeply_nested_file_does_not_print_a_traceback_on_every_poll(ext, parses, capsys):
    drop(ext.incoming, "deep", "name: deep\nformula: refunds / orders\nsystems: " + "[" * 3000 + "]" * 3000 + "\n")
    drop(ext.incoming, "refund_rate", page_yaml())
    results = look(ext, 6)
    assert [r["name"] for r in results] == ["refund_rate"]               # the other file is not held up
    assert "Traceback" not in capsys.readouterr().err
    assert len(parses) == 2


@pytest.mark.parametrize("name, with_sql", [("ts", False), ("value", True), ("value", False), ("country", False),
                                            ("platform", True)])
def test_a_name_that_is_a_column_of_the_data_is_refused(ext, name, with_sql, capsys):
    """V5. Such a metric would overwrite a key column of every frame the brain builds."""
    drop(ext.incoming, "taken", definition(name, RATE_SQL if with_sql else None, formula="refunds / orders"))
    [r] = look(ext)
    assert r["status"] == "rejected" and "is a column of the data" in r["message"], r["message"]
    assert name not in ext.graph.specs and "Traceback" not in capsys.readouterr().err


def test_a_complete_definition_takes_the_place_of_a_tentative_one_with_the_same_formula(ext):
    """V6. A query that returns part of the rows is registered with the formula it matches; the whole
    definition that comes later is not its alias."""
    drop(ext.incoming, "de_only", definition("de_only", RATE_SQL, where="WHERE country = 'DE'", formula="refunds / orders"))
    [first] = look(ext)
    assert first["status"] == "tentative"
    drop(ext.incoming, "refund_rate", page_yaml())
    results = {r["name"]: r for r in look(ext, 4)}
    r = results["refund_rate"]
    assert r["status"] == "validated", r["message"]
    assert by_check(r)["alias"]["status"] == "pass" and "de_only" in by_check(r)["alias"]["detail"]
    assert results["de_only"]["status"] == "rejected" and results["de_only"]["alias_of"] == "refund_rate"
    assert answer(ext, "de_only") == results["de_only"]
    assert contributed(ext) == ["refund_rate"]
    assert {e.dst for e in ext.graph.out_edges("refunds", "formula")} == {"orders", "refund_rate"}


def test_a_tentative_definition_does_not_displace_a_tentative_one(ext):
    for stem, country in (("de_only", "DE"), ("fr_only", "FR")):
        drop(ext.incoming, stem, definition(stem, RATE_SQL, where=f"WHERE country = '{country}'", formula="refunds / orders"))
        look(ext)
    assert answer(ext, "de_only")["status"] == "tentative"
    r = answer(ext, "fr_only")
    assert r["status"] == "rejected" and r["alias_of"] == "de_only" and "tentative" in r["message"]
    assert contributed(ext) == ["de_only"]


def test_two_definitions_that_would_both_be_tentative_do_not_take_each_others_place(market, tmp_path):
    """Both have no caveat until their place is looked for, and there it is ambiguous for both."""
    core = [spec_from_dict(raw) for raw in (
        {"name": "orders", "column": "orders", "unit": "count", "source": "orders"},
        {"name": "purchases", "column": "orders", "unit": "count", "source": "orders"},
        {"name": "refunds", "column": "refunds", "unit": "count", "source": "orders"})]
    store = Store(market)
    ext = Extension(MetricGraph.from_specs(core), store, tmp_path / "incoming", graph_out=tmp_path / "graph.json", quiet=True)
    drop(ext.incoming, "refund_rate", with_formula())
    [first] = look(ext)
    drop(ext.incoming, "refund_share", with_formula().replace("name: refund_rate", "name: refund_share"))
    [second] = look(ext)
    later = look(ext, 6)
    store.conn.close()
    assert first["status"] == "tentative" and "ambiguous" in first["message"]
    assert second["status"] == "rejected" and second["alias_of"] == "refund_rate" and "tentative" in second["message"]
    assert [c["check"] for c in second["checks"] if c["check"] == "alias"] == ["alias"]
    assert by_check(second)["alias"]["status"] == "fail"
    assert later == [] and contributed(ext) == ["refund_rate"]
