"""The extension agent: a new metric arrives as a definition and a query, and finds its place by itself.

Someone (the console's button, or a person) puts `<name>.yaml` into the incoming folder; the shape is
docs/contracts/metric_example.yaml. The live monitor calls `Extension.poll()` once per poll. The answer
is `<name>.result.json` next to the YAML (docs/contracts/README.md, item 5), and the graph export is
written again when the graph changed. No language model is used.

When a file is taken. The console writes the file in place, so a look can catch it empty or half
written. An empty file and a file that is not readable YAML are never answered: an empty one is looked
at again on the next poll, an unreadable text is parsed once and read again when it changes. A
readable file is taken when it reads the same on two looks in a row. A file is remembered by its
content: the same text written again does nothing, a changed text is taken again and replaces what the
file defined before (a text that is rejected withdraws the metric). A file above 64 KB is not a
definition: it is answered (rejected) without being parsed. Files of one look can build on each other
whatever their names: a definition that uses a metric the graph lacks is judged again after the other
files of the look, waits while another file is still being written, and only then gets its rejection.

What is done with a definition, in this order; every step leaves a check with its numbers:

  read        a mapping with a new lower_snake_case name and a formula, a query or both; the formula
              uses metrics the graph has; the name is not a column of the data (ts, value, a dimension,
              a measure); a tolerance, when given, is above 0 and at most 1e-4, so a definition cannot
              loosen the comparison of its own query with its formula
  sql         the query runs inside the contract tables with a time and a row limit and returns ts,
              value and the dimensions it groups by, one row per hour and segment
  formula     the given one; with a query only, every formula of the shapes "a", "a / b", "a * b",
              "a + b", "a - b" over the column metrics is computed on the same rows and the one that
              reproduces every value of the query is taken (several: the first, and it is said)
  definition  the query against the formula at the query's grain, row by row
  rows        both directions: rows the query has and the facts lack (rejected), rows the facts have
              and the query lacks or leaves empty (a filtered metric: tentative)
  split       is the query a function of sums? At the grain of the facts a group is one fact row, so
              an average, a maximum or a count of rows equals the formula there and the row-by-row
              comparison cannot tell. The query is run again on a private in-memory copy of the last
              24 hours (Store.rerun_on_split_rows), as they are and with every fact row split in two:
              a function of sums returns the same values, anything else changes (tentative)
  alias       its numeric signature (brain/signature.py) against every metric: the same definition
              under another name is reported and not added. One exception: when the twin is a
              tentative contribution and this definition has no caveat, this one takes the place and
              the twin's file is judged again (it is then the alias)
  rollup      what the breakdown links promise: the values in the segments, added up (an additive
              metric) or weighted by the denominator (a ratio of two additive metrics), give the
              formula on the totals, for every dimension; segments with a numerator and a zero
              denominator have no value of their own and are counted. Once the query equals the
              formula row by row this holds by algebra; the test of the query itself is `split`
  coverage    brain/validate.py on the whole graph with the metric in it (also bounds, when declared)
  placement   identities that hold for any data and link the new metric m to two existing ones, found
              by signature. A product or a sum (x = y * m, x = y + m) makes x a whole and m a part of
              it; when x has no decomposition of its own and is outside the outcome tree, it is
              applied: x now decomposes into m, and m is registered as a leaf. Two such places:
              ambiguous, none is applied, the status is tentative. x inside the outcome tree, or m
              itself tentative: proposed, left to a person. A difference (x = y - m, x = m - y) and
              an x that already decomposes are kept as alternatives with the reason
  context     the metric takes the context of the metrics its validated formula is built from: the union
              of their release systems joins its own (so a release of one of them is a candidate cause of
              a change in it, and a calendar system brings the calendar link that explains a planned event);
              their data sources reach it through the formula (freshness). A metric that equals a product
              of two or three watched rates for any data (by signature) takes their systems too. The check
              names each and its origin
  monitoring  a ratio of two column metrics is registered as an atomic rate, the shape the monitor
              watches. The monitor builds its watch list and its columns once, at start; when it has
              a `rewatch()` it is called after every change of the graph and the message says
              "watched from the next tick", otherwise "registered, not watched yet". Any other shape
              stays in the graph as validated, and the message says "not watched: only ratios of two
              columns are watched"

Statuses: `validated` (in the graph, every check passed), `tentative` (in the graph, with a caveat the
message names), `rejected` (not in the graph; the message says why).

Nothing here names a metric, a dimension value or a kind of fault; every rule is about the shape of a
definition and about what the data shows.
"""
from __future__ import annotations

import difflib
import hashlib
import itertools
import re
import time
import traceback
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from brain.clock import HOUR
from brain.data import READABLE_TABLES, TS_FORMAT, Store, iso_hour
from brain.evaluator import Evaluator, NotComputable
from brain.formula import parse
from brain.graph import CALENDAR_SYSTEMS, GraphError, MetricGraph, tree_dict
from brain.incidents import write_json
from brain.metric_spec import DIMENSIONS, NAME_RE, MetricSpec, SpecError, spec_from_dict
from brain.monitor import watch_list
from brain.signature import N_POINTS, same, signatures
from brain.validate import rel_error, validate_graph

SETTLE_LOOKS = 2          # a readable file is taken when it reads the same on this many looks in a row
MAX_BYTES = 64 * 1024     # a definition file is at most this large; a larger one is answered without being parsed
MAX_TOLERANCE = 1e-4      # the loosest tolerance a definition may ask for itself (the default is 1e-6)
SQL_TIMEOUT = 3.0         # seconds a contributed query may run: the monitor's loop waits for it (the page's takes 0.3)
SQL_MAX_ROWS = 500_000    # rows it may return (28 days at the finest grain are about 121,000)
SPLIT_HOURS = 24          # the query is run again on a copy of this many hours with every fact row split in two
SPLIT_TOL = 1e-9          # a function of sums differs between the two runs by rounding only
VALIDATE_DAYS = 7         # the window brain/validate.py checks the graph on, as at the monitor's start
MAX_ATTEMPTS = 3          # looks on which an unexpected error is tried again before the file is answered with it
ABS_TOL = 1e-9            # two values this close are equal whatever their size (sums of money that cancel)
PROBE_ROWS = 256          # a candidate formula is first tried on this many rows
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:00:00Z$")
NOT_WATCHED = "registered, not watched yet"
WATCHED = "watched from the next tick"
OTHER_SHAPE = "not watched: only ratios of two columns are watched"


class Rejected(Exception):
    """The definition cannot be registered. `check` names the step that failed."""

    def __init__(self, check: str, reason: str, **extra):
        super().__init__(reason)
        self.check, self.reason, self.extra = check, reason, extra


class Later(Exception):
    """The definition uses a metric the graph does not have, and another file of this look may bring it."""


@dataclass
class Accepted:
    """What one file added to the graph."""
    spec: MetricSpec
    status: str                        # validated | tentative
    parent: str | None = None          # an existing metric that now decomposes into the new one ...
    decomposition: str | None = None   # ... and how
    source: str = ""                   # the file


@dataclass
class Reading:
    """One reading of the data at a grain: the base-column sums of every hour and segment, and next to
    them the value the query returned for it."""
    frame: pd.DataFrame                # keys, base columns, value, in_facts, in_sql
    dims: list[str]
    first: pd.Timestamp
    end: pd.Timestamp
    seconds: float = 0.0               # how long the query ran
    sql_rows: int = 0
    keys: list[str] = field(init=False)

    def __post_init__(self):
        self.keys = ["ts"] + self.dims

    @property
    def span(self) -> str:
        hours = int((self.end - self.first) / HOUR)
        return f"{hours / 24:.0f} days" if hours >= 48 else f"{hours} hours"


def _check(name: str, status: str, detail: str) -> dict:
    return {"check": name, "status": status, "detail": detail}


def _words(items) -> str:
    items = [str(i) for i in items]
    return ", ".join(items[:-1]) + " and " + items[-1] if len(items) > 1 else "".join(items)


# ------------------------------------------------------------------------------------ formula search
_OPS = {
    "name": lambda a, b: a,
    "ratio": lambda a, b: a / b,
    "product": lambda a, b: a * b,
    "sum": lambda a, b: a + b,
    "difference": lambda a, b: a - b,
}


def candidates(names: list[str]):
    """Formulas over the column metrics, simplest first: (text, shape, a, b)."""
    for a in names:
        yield a, "name", a, None
    for a, b in itertools.permutations(names, 2):
        yield f"{a} / {b}", "ratio", a, b
    for a, b in itertools.combinations(names, 2):
        yield f"{a} * {b}", "product", a, b
    for a, b in itertools.combinations(names, 2):
        yield f"{a} + {b}", "sum", a, b
    for a, b in itertools.permutations(names, 2):
        yield f"{a} - {b}", "difference", a, b


def _equal(v: np.ndarray, c: np.ndarray, tol: float) -> np.ndarray:
    """Row by row: the candidate has a value and it is the query's value. NaN is never equal."""
    return np.abs(v - c) <= tol * np.maximum(np.abs(v), np.abs(c)) + ABS_TOL


def find_formulas(value: np.ndarray, columns: dict[str, np.ndarray], tol: float) -> tuple[list[str], int, int]:
    """Every candidate that reproduces all the values the query returned, how many were tried and on
    how many values. Deterministic: the candidates are enumerated, computed and compared."""
    rows = np.isfinite(value)
    if not rows.any():
        return [], 0, 0
    v = value[rows]
    cols = {n: a[rows] for n, a in columns.items()}
    probe = slice(0, PROBE_ROWS)
    fits, tried = [], 0
    with np.errstate(divide="ignore", invalid="ignore"):
        for text, shape, a, b in candidates(list(cols)):
            tried += 1
            op, x, y = _OPS[shape], cols[a], cols[b] if b else None
            if not _equal(v[probe], op(x[probe], y[probe] if b else None), tol).all():
                continue
            if _equal(v, op(x, y), tol).all():
                fits.append(text)
    return fits, tried, int(rows.sum())


# ------------------------------------------------------------------------------- comparing row sets
def compare(reading: Reading, calc: np.ndarray, tol: float) -> dict:
    """The query's values against the formula's, and the two row sets against each other."""
    f = reading.frame
    value = f["value"].to_numpy(float)
    in_sql, in_facts = f["in_sql"].to_numpy(bool), f["in_facts"].to_numpy(bool)
    has_v, has_c = np.isfinite(value), np.isfinite(calc)
    both = has_v & has_c
    with np.errstate(invalid="ignore"):
        bad = both & ~_equal(value, calc, tol)
    worst, _ = rel_error(value[both], calc[both]) if both.any() else (float("nan"), 0)
    return {"compared": int(both.sum()), "bad": int(bad.sum()), "worst": worst,
            "facts": int(in_facts.sum()), "sql": int(in_sql.sum()), "formula_values": int(has_c.sum()),
            "only_sql_rows": int((in_sql & ~in_facts).sum()),          # the query has a row the facts lack
            "only_fact_rows": int((in_facts & ~in_sql).sum()),         # the facts have a row the query lacks
            "sql_value_only": int((has_v & ~has_c & in_facts).sum()),  # a value where the formula has none
            "formula_value_only": int((has_c & ~has_v & in_sql).sum()),  # a row left empty where the formula has a value
            "both_empty": int((in_sql & in_facts & ~has_v & ~has_c).sum())}


# ------------------------------------------------------------------------------------- placement
# (identity, decomposition of x, how y and the new metric m combine, role of m, x is the whole)
# A product or a sum makes x the whole and m a part of it: that can become x's decomposition. A difference
# makes x what is left of something: it is true, it is reported, and it is never applied.
_FORMS = (
    ("{x} = {y} * {m}", "{y} * {m}", lambda y, m: y * m, "a factor", True),
    ("{x} = {y} + {m}", "{y} + {m}", lambda y, m: y + m, "a term", True),
    ("{x} = {y} - {m}", "{y} - {m}", lambda y, m: y - m, "a term", False),
    ("{x} = {m} - {y}", "{m} - {y}", lambda y, m: m - y, "a term", False),
)


def identities(base: MetricGraph, sig: dict[str, np.ndarray], name: str) -> list[dict]:
    """Identities that hold for any data and link the new metric to two existing ones. Found on the
    numeric signatures, so no data is read and nothing is fitted."""
    names = [m for m in base.specs if m in sig]
    if not names:
        return []
    table = np.array([sig[m] for m in names])
    out = []
    for text, decomposition, fn, role, whole in _FORMS:
        made = fn(table, sig[name])                                    # row j: y_j combined with the new metric
        hit = np.isclose(table[:, None, :], made[None, :, :], rtol=1e-9, atol=0.0).all(axis=2)
        for i, j in zip(*np.nonzero(hit)):
            if i != j and np.all(np.isfinite(made[j])):
                x, y = names[i], names[j]
                out.append({"parent": x, "other": y, "role": role, "whole": whole,
                            "identity": text.format(x=x, y=y, m=name),
                            "decomposition": decomposition.format(y=y, m=name)})
    return out


# ---------------------------------------------------------------------------------------- export
def export(graph: MetricGraph) -> dict:
    """The graph for the console. The trees of the declared outcomes come first, exactly as the graph
    gives them; then one tree for everything a contribution added that those trees do not show. What a
    tree above already shows is marked `repeated` and not drawn again."""
    out = graph.to_dict()
    shown: set[str] = set()

    def collect(t: dict) -> None:
        shown.add(t["metric"])
        for c in t["children"]:
            collect(c)

    for t in out["trees"]:
        collect(t)
    for root in graph.unplaced_roots():
        out["trees"].append(tree_dict(graph.tree(root, shown)))
    for name, spec in graph.specs.items():
        if spec.origin == "core" or name in shown:
            continue
        # an atomic rate that nothing decomposes into: drawn with what it is defined from
        shown.add(name)
        children = []
        for e in graph.out_edges(name, "definition"):
            children.append({"metric": e.dst, "role": e.attrs.get("role"), "repeated": e.dst in shown, "children": []})
            shown.add(e.dst)
        out["trees"].append({"metric": name, "role": None, "repeated": False, "children": children})
    return out


# ------------------------------------------------------------------------------------- the agent
class Extension:
    def __init__(self, graph: MetricGraph, store: Store, incoming: str | Path, graph_out: str | Path | None = None,
                 monitor=None, fresh: bool = False, quiet: bool = False, sql_timeout: float = SQL_TIMEOUT,
                 settle: int = SETTLE_LOOKS):
        self.graph = graph                      # the object the monitor holds: accepted metrics are adopted into it
        self.store = store
        self.incoming = Path(incoming)
        self.graph_out = Path(graph_out) if graph_out else None
        self.monitor = monitor
        self.quiet = quiet
        self.sql_timeout = sql_timeout
        self.settle = settle
        self.core: list[MetricSpec] = list(graph.specs.values())   # the graph as it was loaded
        self.accepted: dict[str, Accepted] = {}                    # file stem -> what the file added
        self._seen: dict[str, tuple[str, int]] = {}                # file name -> (content, looks in a row)
        self._done: dict[str, str] = {}                            # file name -> content that has its answer
        self._errors: dict[str, int] = {}                          # content -> unexpected errors so far
        self._told: dict[str, str] = {}                            # file name -> content we said is not readable
        if fresh and self.incoming.is_dir():
            # answers of an earlier run; the definitions stay and are taken again
            for p in list(self.incoming.glob("*.result.json")) + list(self.incoming.glob("*.result.json.tmp")):
                p.unlink(missing_ok=True)

    # ------------------------------------------------------------------------------- the loop
    def poll(self) -> list[dict]:
        """One look at the incoming folder; returns the answers written during it. Never raises: a
        definition gets its answer or waits for the next look, and the monitor goes on."""
        written: list[dict] = []
        try:
            paths = sorted(self.incoming.glob("*.yaml")) if self.incoming.is_dir() else []
        except OSError:
            return written
        try:
            waiting = self._round(paths, written)
            # A definition built on a metric the graph does not have may find it in another file of this
            # look (after a restart every file is taken at once, in the order of the names): it is judged
            # again after the others, for as long as a round brings one more of them in.
            while waiting:
                still = self._round(waiting, written)
                if len(still) < len(waiting):
                    waiting = still
                    continue
                being_written = any(self._seen.get(p.name, ("", self.settle))[1] < self.settle for p in paths)
                if not being_written:        # nothing more can come in this look: answered as they stand
                    self._round(still, written, last=True)
                break
        except Exception:
            traceback.print_exc()
        return written

    def _round(self, paths: list[Path], written: list[dict], last: bool = False) -> list[Path]:
        """Look at each file once; returns the files that wait for a metric another file may bring."""
        waiting = []
        for path in paths:
            try:
                result = self._look(path, last)
                if result is not None:
                    written.append(result)
                    self._say(result["message"])
            except Later:
                waiting.append(path)
            except Exception:    # one file must not stop the others, or the monitor
                traceback.print_exc()
        return waiting

    def _say(self, text: str) -> None:
        if self.quiet:
            return
        line = f"extension: {text}"
        try:
            print(line, flush=True)
        except UnicodeEncodeError:           # a console that lacks a character of the definition (cp1251 on Windows)
            print(line.encode("ascii", "backslashreplace").decode("ascii"), flush=True)

    def _look(self, path: Path, last: bool = False) -> dict | None:
        try:
            with path.open("rb") as f:
                data = f.read(MAX_BYTES + 1)
            stat = path.stat()
        except OSError:                      # being written or replaced right now
            return None
        key, answer = path.name, self._answer_path(path)
        if not data.strip():                 # opened by its writer, nothing in it yet
            self._seen.pop(key, None)
            return None
        large = len(data) > MAX_BYTES        # not read to its end and never parsed: known by its size and time
        content = f"{stat.st_size} bytes at {stat.st_mtime_ns}" if large else hashlib.sha256(data).hexdigest()
        if self._done.get(key) == content and answer.exists():
            self._seen.pop(key, None)
            return None                      # the same text again: it has its answer
        seen = self._seen.get(key)
        looks = seen[1] + 1 if seen and seen[0] == content else 1
        self._seen[key] = (content, looks)
        if looks == 1:
            try:
                answer.unlink(missing_ok=True)   # an answer that is there is about another text
            except OSError:
                pass                             # a reader holds it; it is replaced when the new answer is written
        if looks < self.settle:
            return None
        if large:
            reason = f"the file is {stat.st_size} bytes; a definition is at most {MAX_BYTES // 1024} KB"
            self._withdraw(path.stem)
            result = self._result(path.stem, "rejected", [_check("read", "fail", reason)],
                                  f"{path.stem} rejected: {reason}", source=path.name)
            return self._answered(key, content, answer, result)
        if self._told.get(key) == content:
            return None                      # this text was not readable and has not changed: not parsed again
        try:
            raw = yaml.safe_load(data.decode("utf-8"))
        except (UnicodeDecodeError, yaml.YAMLError, RecursionError) as e:
            self._not_ready(key, content, (str(e).splitlines() or [type(e).__name__])[0])
            return None
        if raw is None or isinstance(raw, (str, int, float, bool)):
            self._not_ready(key, content, "it holds no fields yet")
            return None

        try:
            result = self._judge(path, raw, last)
        except Later:
            raise
        except Exception as e:
            # a busy database or a fault of ours: try again a few times, then answer with the error
            self._errors[content] = self._errors.get(content, 0) + 1
            traceback.print_exc()
            if self._errors[content] < MAX_ATTEMPTS:
                return None
            self._withdraw(path.stem)
            reason = f"it could not be checked: {type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"
            result = self._result(path.stem, "rejected", [_check("internal", "fail", reason)],
                                  f"{path.stem} rejected: {reason}", source=path.name)
        return self._answered(key, content, answer, result)

    def _answered(self, key: str, content: str, answer: Path, result: dict) -> dict:
        write_json(answer, result)
        self._done[key] = content
        self._seen.pop(key, None)
        return result

    def _not_ready(self, key: str, content: str, why: str) -> None:
        if self._told.get(key) != content:
            self._told[key] = content
            self._say(f"{key} is not a readable definition yet ({why}); it is read again when it changes")

    def _answer_path(self, path: Path) -> Path:
        return path.with_name(path.stem + ".result.json")

    @staticmethod
    def _result(name: str, status: str, checks: list[dict], message: str, placed: str = "", links=(), **more) -> dict:
        return {"name": name, "status": status,
                "placed": placed or "not placed: the definition was rejected",
                "links_proposed": list(links), "checks": checks, "message": " ".join(message.split()),
                "formula": more.pop("formula", None), "formula_source": more.pop("formula_source", None),
                "alias_of": more.pop("alias_of", None), **more}

    # --------------------------------------------------------------------------- the registry
    def _specs(self, accepted: dict[str, Accepted]) -> list[MetricSpec]:
        """The core metrics, with the decompositions that placements gave some of them, then the contributions."""
        given = {a.parent: parse(a.decomposition) for a in accepted.values() if a.parent}
        specs = [replace(s, decomposition=given[s.name]) if s.name in given else s for s in self.core]
        return specs + [a.spec for a in accepted.values()]

    def _others(self, stem: str, without: tuple[str, ...] = ()) -> dict[str, Accepted]:
        """What the other files added (`without`: except these). A contribution that was built on one
        that is being replaced cannot stay as it is: it leaves the graph and its file is judged again on
        the next look."""
        others = {s: a for s, a in self.accepted.items() if s != stem and s not in without}
        while True:
            try:
                MetricGraph.from_specs(self._specs(others))
                return others
            except GraphError:
                names = {s.name for s in self.core} | {a.spec.name for a in others.values()}
                orphans = [s for s, a in others.items()
                           if any(n not in names for f in (a.spec.formula, a.spec.decomposition) if f for n in f.names)
                           or any(l.metric not in names for l in a.spec.may_affect)]
                if not orphans:
                    raise
                for s in orphans:
                    self._done.pop(others.pop(s).source, None)

    def _withdraw(self, stem: str) -> None:
        """Take what a file added out of the graph again (its definition could not be checked)."""
        if stem not in self.accepted:
            return
        try:
            others = self._others(stem)
            trial, _ = self._adopt(others)
            self._publish(others, trial)
        except Exception:
            traceback.print_exc()

    def _adopt(self, accepted: dict[str, Accepted]) -> tuple[MetricGraph, list]:
        """Build the graph with these contributions and validate all of it on the data. The queries are
        left out of that: each was compared with its formula on all the data when its file was judged,
        and validate_graph would run every one of them again, without a limit, on every change."""
        specs = self._specs(accepted)
        trial = MetricGraph.from_specs(specs)
        probe = MetricGraph.from_specs([replace(s, sql=None) if s.origin != "core" else s for s in specs])
        checks = validate_graph(probe, self.store, days=VALIDATE_DAYS)
        trial.status.update(probe.status)     # the same metrics and links: the statuses are theirs
        for a in accepted.values():
            if a.status == "tentative":
                trial.status[a.spec.name] = "tentative"
        return trial, checks

    def _publish(self, accepted: dict[str, Accepted], trial: MetricGraph) -> set[str]:
        """Make the graph the monitor holds this one, write the export, and tell the monitor.
        Returns the rates the monitor watches after that (empty when it cannot take new ones)."""
        self.accepted = accepted
        self.graph.adopt(trial)
        if self.graph_out is not None:
            write_json(self.graph_out, export(self.graph))
        rewatch = getattr(self.monitor, "rewatch", None)   # the monitor builds its watch list once, at start
        if rewatch is None:
            return set()
        rewatch()
        return {w.metric for w in self.monitor.watched}

    # ------------------------------------------------------------------------------ one definition
    def _judge(self, path: Path, raw, last: bool = True) -> dict:
        """`last`: nothing more can bring a metric the definition lacks; otherwise it may raise Later."""
        checks: list[dict] = []
        stem = path.stem
        others = self._others(stem)
        try:
            return self._examine(path, raw, others, checks, last)
        except Rejected as e:
            checks.append(_check(e.check, "fail", e.reason))
            given = raw.get("name") if isinstance(raw, dict) else None
            name = given if isinstance(given, str) and NAME_RE.match(given) else stem
            if set(others) != set(self.accepted):   # the file defined a metric before: it is withdrawn with its links
                trial, _ = self._adopt(others)
                self._publish(others, trial)
            return self._result(name, "rejected", checks, f"{name} rejected: {e.reason}", **{"source": path.name, **e.extra})

    def _examine(self, path: Path, raw, others: dict[str, Accepted], checks: list[dict], last: bool = True,
                 gave_way: Accepted | None = None) -> dict:
        stem = path.stem
        base_specs = self._specs(others)
        base = MetricGraph.from_specs(base_specs)

        # ---- read
        if not isinstance(raw, dict):
            raise Rejected("read", "a metric definition must be a mapping of fields (name, formula, sql, ...), "
                                   f"got a {type(raw).__name__}")
        name = raw.get("name")
        if name is None and "metrics" in raw:
            raise Rejected("read", "one metric per file: give each definition its own <name>.yaml with name at the top level")
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise Rejected("read", f"bad metric name {name!r}: use lower_snake_case, starting with a letter")
        if not (raw.get("formula") or raw.get("sql")):
            raise Rejected("read", "it needs a formula or a SQL query; the file gives neither")
        if raw.get("sql") is not None and not isinstance(raw["sql"], str):
            raise Rejected("read", f"sql must be one query as text, got a {type(raw['sql']).__name__}")
        if name in base.specs:
            owner = next((f"defined by {a.source}" for a in others.values() if a.spec.name == name), "a core metric")
            raise Rejected("read", f"the name already exists in the graph ({owner}); a new metric needs a new name")
        if name in ("ts", "value") + DIMENSIONS or name in self.store.columns:
            # every table the brain builds has these as its keys; a metric of that name would overwrite one
            raise Rejected("read", f"the name {name} is a column of the data; a metric needs another name")
        if "tolerance" in raw:
            try:
                tolerance = float(raw["tolerance"])
            except (TypeError, ValueError):
                tolerance = float("nan")
            if not 0 < tolerance <= MAX_TOLERANCE:   # with 1 any two values of the same sign are "equal"
                raise Rejected("read", f"tolerance must be a number above 0 and at most {MAX_TOLERANCE:g}, got "
                                       f"{raw['tolerance']!r}: a definition may not loosen the comparison of its own "
                                       f"query with its formula")
        try:
            spec = spec_from_dict(raw, origin="contributed")
        except SpecError as e:
            raise Rejected("read", str(e).removeprefix(f"{name}: ")) from None
        except (TypeError, ValueError) as e:
            raise Rejected("read", f"a field has a value of the wrong kind: {e}") from None
        if spec.column:
            raise Rejected("read", "a contributed metric is defined by a formula or a query, not by a raw column")
        for label, f in (("formula", spec.formula), ("decomposition", spec.decomposition)):
            missing = [n for n in (f.names if f else ()) if n not in base.specs]
            if missing and not last:
                raise Later()
            if missing:
                near = [m for n in missing for m in difflib.get_close_matches(n, list(base.specs), n=1)]
                raise Rejected("read", f"the {label} uses unknown metrics {missing}"
                                       + (f" (closest in the graph: {_words(near)})" if near else ""))
        checks.append(_check("read", "pass", f"definition read: {_words(sorted(raw))}"))

        # ---- sql, formula, definition, rows: a second reading when the first one disagrees, because the
        # newest hours can be rewritten between the query and the read of the facts
        base_cols = {m: s.column for m, s in base.specs.items() if s.column and s.column in self.store.measures}
        formula, source, fits, tried, valued = spec.formula, "given", [], 0, 0
        for attempt in (1, 2):
            reading = self._read(spec, "dimensions" in raw, sorted(set(base_cols.values())))
            if spec.formula is None:
                value = reading.frame["value"].to_numpy(float)
                columns = {m: reading.frame[c].to_numpy(float) for m, c in base_cols.items()}
                fits, tried, valued = find_formulas(value, columns, spec.tolerance)
                if not fits and attempt == 1:
                    continue
                if not fits:
                    raise Rejected("formula", f"no formula found: none of {tried} candidates (a column metric, or a ratio, "
                                              f"product, sum or difference of two of the {len(columns)}) reproduces the "
                                              f"{valued} values the SQL returns")
                formula, source = parse(fits[0]), "inferred"
            candidate = replace(spec, formula=formula, dimensions=tuple(reading.dims))
            try:
                trial = MetricGraph.from_specs(base_specs + [candidate])
                evaluator = Evaluator(trial, self.store)
                calc = self._formula_values(evaluator, reading, name)
            except (GraphError, NotComputable) as e:
                raise Rejected("formula", str(e).removeprefix(f"{name}: ")) from None
            stats = compare(reading, calc, spec.tolerance)
            if not spec.sql or not (stats["bad"] or stats["sql_value_only"] or stats["only_sql_rows"]):
                break
        spec = candidate
        grain = ", ".join(reading.keys)
        extra = {"formula": formula.text, "formula_source": source, "source": path.name,
                 "data": {"start": iso_hour(reading.first), "end": iso_hour(reading.end),
                          "hours": int((reading.end - reading.first) / HOUR)}}

        if spec.sql:
            checks.append(_check("sql", "pass", f"the query ran in {reading.seconds:.2f} s inside the contract tables "
                                                f"and returned {reading.sql_rows} rows at grain {grain}"))
        else:
            checks.append(_check("sql", "skip", "no SQL was given"))
        if source == "given":
            checks.append(_check("formula", "pass", f"given: {formula.text}"))
        elif len(fits) == 1:
            checks.append(_check("formula", "pass", f"found {formula.text}: the only one of {tried} candidates (a column "
                                                    f"metric, or a ratio, product, sum or difference of two) that "
                                                    f"reproduces all {valued} values of the query"))
        else:
            checks.append(_check("formula", "warn", f"{len(fits)} of {tried} candidates reproduce all {valued} values of "
                                                    f"the query: {_words(fits[:5])}; took {formula.text}, the first; more "
                                                    f"data would tell them apart"))
        self._row_checks(spec, stats, grain, checks, extra)
        checks.append(self._split(spec, reading, grain))

        # ---- alias
        sig = signatures(trial)
        if name not in sig:
            raise Rejected("formula", f"{formula.text} cannot be computed from the columns of facts_hourly", **extra)
        twin = next((m for m in base.specs if m in sig and same(sig[m], sig[name])), None)
        if twin is not None:
            holder = next((s for s, a in others.items() if a.spec.name == twin and a.status == "tentative"), None)
            if holder is not None and not any(c["status"] == "warn" for c in checks):
                # the twin is in the graph with a caveat and this definition has none so far: it is judged
                # without the twin and, when it gets in, takes the place; the twin's file is judged again
                del checks[:]
                return self._examine(path, raw, self._others(stem, (holder,)), checks, last, others[holder])
            raise self._alias(name, formula.text, twin, others[holder].source if holder is not None else "", extra)
        checks.append(_check("alias", "pass", f"no existing metric has this definition: its signature on {N_POINTS} random "
                                              f"points differs from each of the {len(base.specs)} metrics"))

        # ---- rollup
        fine = reading.frame["value"].to_numpy(float) if spec.sql else calc
        checks.append(self._rollup(spec, trial, evaluator, reading, fine))

        # ---- placement, then the whole graph with the metric in it
        settled = not any(c["status"] == "warn" for c in checks)
        final, found, applied = self._place(base, base_specs, others, spec, raw, sig, settled)
        final, context = self._inherit(base, final, sig)
        status ="validated" if settled and not any(c["status"] == "ambiguous" for c in found) else "tentative"
        if gave_way is not None and status != "validated":
            # it would be tentative as well: the one that was there first stays (and two such definitions
            # would otherwise take each other's place for ever)
            del checks[[c["check"] for c in checks].index("alias"):]
            raise self._alias(name, formula.text, gave_way.spec.name, gave_way.source, extra)
        mine = Accepted(final, status, applied["parent"] if applied else None,
                        applied["decomposition"] if applied else None, path.name)
        accepted = {**others, stem: mine}
        try:
            trial, validation = self._adopt(accepted)
        except GraphError as e:
            raise Rejected("placement", str(e).removeprefix(f"{name}: "), **extra) from None
        for c in validation:
            if c.metric == name and c.status == "fail":
                raise Rejected(c.check, c.detail, **extra)
            if c.metric == name and c.check in ("coverage", "bounds"):
                checks.append(_check(c.check, c.status, f"{c.detail} (last {VALIDATE_DAYS} days, all segments together)"))
        proven = next((c.detail for c in validation if applied and c.metric == applied["parent"] and c.check == "identity"), "")
        checks.append(self._placement_check(found, applied, proven))
        checks.append(context)

        # ---- it is in: publish, and see whether the monitor takes it
        watching = self._publish(accepted, trial)
        if gave_way is not None:
            self._done.pop(gave_way.source, None)
            next(c for c in checks if c["check"] == "alias")["detail"] += (
                f"; the tentative contribution {gave_way.spec.name} ({gave_way.source}) has the same formula and gives "
                f"way to this definition, which has no caveat: its file is judged again")
        rate = next((w for w in watch_list(self.graph) if w.metric == name), None)
        if rate is None:
            checks.append(_check("monitoring", "skip", f"{OTHER_SHAPE}, and {spec.formula.text} is not one: the "
                                                       f"metric is in the graph and validated, the hourly scan does not "
                                                       f"judge it"))
            watch = OTHER_SHAPE
        elif name in watching:
            checks.append(_check("monitoring", "pass", f"an atomic rate of two columns ({rate.numerator} / "
                                                       f"{rate.denominator}): {WATCHED}"))
            watch = WATCHED
        else:
            why = "the monitor builds its watch list once, at start" if self.monitor is not None else "no monitor is attached"
            checks.append(_check("monitoring", "skip", f"an atomic rate of two columns ({rate.numerator} / "
                                                       f"{rate.denominator}): {NOT_WATCHED} ({why})"))
            watch = NOT_WATCHED

        links = self._links(name, found)
        placed = self._placed_sentence(final, found, applied)
        message = self._message(name, status, spec, source, stats, reading, checks, found, applied, watch,
                                len(fits), tried)
        return self._result(name, status, checks, message, placed=placed, links=links, **extra)

    @staticmethod
    def _alias(name: str, formula: str, twin: str, source: str, extra: dict) -> Rejected:
        """`source`: the file of the twin when the twin is a tentative contribution."""
        what = f"the tentative contribution {twin} ({source})" if source else twin
        return Rejected("alias", f"it is an alias of {what}: {formula} gives the same value as {twin} for any data, so "
                                 f"no new node was added", alias_of=twin,
                        placed=f"not placed: {name} is the existing metric {twin} under another name", **extra)

    # ----------------------------------------------------------------------------------- reading
    def _query(self, spec: MetricSpec, declared: bool) -> tuple[pd.DataFrame, list[str], float]:
        began = time.monotonic()
        try:
            df = self.store.read_sql(spec.sql, timeout=self.sql_timeout, max_rows=SQL_MAX_ROWS)
        except PermissionError:
            raise Rejected("sql", "SQL refused: a definition may read only the contract tables "
                                  + ", ".join(READABLE_TABLES)) from None
        except TimeoutError:
            raise Rejected("sql", f"SQL took longer than {self.sql_timeout:g} s and was stopped") from None
        except OverflowError:
            raise Rejected("sql", f"SQL returns more than {SQL_MAX_ROWS} rows") from None
        except Exception as e:   # a query that does not run is a failed definition, whatever the reason
            reason = (str(e.__cause__ or e).strip().splitlines() or [type(e).__name__])[0][:200]
            raise Rejected("sql", f"SQL failed: {reason}") from None
        cols = [str(c) for c in df.columns]
        if "ts" not in cols or "value" not in cols or len(set(cols)) != len(cols):
            raise Rejected("sql", f"SQL must return ts and value once each (and the dimensions it groups by); it returns {cols}")
        dims = [d for d in DIMENSIONS if d in cols]
        other = [c for c in cols if c not in dims + ["ts", "value"]]
        if other:
            raise Rejected("sql", f"SQL returns columns that are neither ts, value nor a dimension: {other}")
        if declared and set(dims) != set(spec.dimensions):
            raise Rejected("sql", f"SQL groups by {dims} but the metric declares dimensions {list(spec.dimensions)}")
        if df.empty:
            raise Rejected("sql", "SQL returns no rows")
        ts = df["ts"].astype(str)
        odd = ~ts.str.match(TS_RE)
        if odd.any():
            raise Rejected("sql", f"SQL must return ts as UTC hour starts like 2026-11-01T13:00:00Z; it returns {ts[odd].iloc[0]!r}")
        value = pd.to_numeric(df["value"], errors="coerce")
        text = value.isna() & df["value"].notna()
        if text.any():
            raise Rejected("sql", f"SQL must return a number in value; it returns {df['value'][text].iloc[0]!r}")
        out = pd.DataFrame({"ts": ts, **{d: df[d].astype(str) for d in dims}, "value": value.astype(float)})
        repeated = int(out.duplicated(["ts"] + dims).sum())
        if repeated:
            raise Rejected("sql", f"SQL returns {repeated} rows that repeat an hour and segment; group by ts and the "
                                  f"dimensions it returns")
        if not np.isfinite(out["value"].to_numpy(float)).any():
            raise Rejected("sql", "SQL returns no values: value is empty in every row")
        return out, dims, time.monotonic() - began

    def _read(self, spec: MetricSpec, declared: bool, columns: list[str]) -> Reading:
        """Run the query (when there is one) and read the facts at the same grain. An hour that arrives
        while this runs is left out on both sides."""
        first, last = self.store.time_range()
        end = last + HOUR
        sql, dims, seconds = None, [d for d in DIMENSIONS if d in spec.dimensions], 0.0
        if spec.sql:
            sql, dims, seconds = self._query(spec, declared)
            sql = sql[sql["ts"] < iso_hour(end)]
        facts = self.store.sums(columns, first, end, group_by=dims, by_hour=True)
        facts["ts"] = facts["ts"].dt.strftime(TS_FORMAT)
        if sql is None:
            frame = facts.assign(value=np.nan, in_facts=True, in_sql=False)
        else:
            frame = facts.merge(sql, on=["ts"] + dims, how="outer", indicator=True)
            frame["in_facts"] = frame["_merge"] != "right_only"
            frame["in_sql"] = frame["_merge"] != "left_only"
            frame = frame.drop(columns="_merge")
        return Reading(frame.reset_index(drop=True), dims, first, end, seconds, 0 if sql is None else len(sql))

    def _formula_values(self, evaluator: Evaluator, reading: Reading, name: str) -> np.ndarray:
        base = sorted(evaluator.base_columns(name))
        derived = evaluator.derive(reading.frame[reading.keys + base], [name])[name]
        return pd.to_numeric(derived, errors="coerce").to_numpy(float)

    def _row_checks(self, spec: MetricSpec, s: dict, grain: str, checks: list[dict], extra: dict) -> None:
        f = spec.formula.text
        if not spec.sql:
            checks.append(_check("definition", "skip", f"no SQL was given: {f} is the definition"))
            checks.append(_check("rows", "skip", f"no SQL to compare with; the formula has a value in {s['formula_values']} "
                                                 f"of {s['facts']} hour-and-segment groups at grain {grain}"))
            return
        if not s["compared"]:
            raise Rejected("definition", f"the SQL and the formula {f} have no row with a value on both sides: there is "
                                         f"nothing to compare", **extra)
        if s["bad"]:
            raise Rejected("definition", f"the SQL and the formula {f} disagree on {s['bad']} of {s['compared']} rows "
                                         f"that have a value on both sides (max relative error {s['worst']:.1e}, "
                                         f"tolerance {spec.tolerance:g})", **extra)
        checks.append(_check("definition", "pass", f"the SQL equals {f} on {s['compared']} rows that have a value (max "
                                                   f"relative error {s['worst']:.1e}, tolerance {spec.tolerance:g}) at "
                                                   f"grain {grain}; {s['both_empty']} rows are empty on both sides"))
        if s["only_sql_rows"] or s["sql_value_only"]:
            raise Rejected("rows", f"the SQL is not {f} on the facts: it returns {s['only_sql_rows']} rows for hours or "
                                   f"segments facts_hourly does not have and {s['sql_value_only']} values where the "
                                   f"formula has none", **extra)
        if s["only_fact_rows"] or s["formula_value_only"]:
            checks.append(_check("rows", "warn", f"the SQL returns only part of the rows: {s['sql']} of {s['facts']} "
                                                 f"hour-and-segment groups of facts_hourly ({s['only_fact_rows']} missing), "
                                                 f"and leaves {s['formula_value_only']} empty where the formula has a "
                                                 f"value; none only in the SQL"))
        else:
            checks.append(_check("rows", "pass", f"the same rows on both sides: {s['facts']} hour-and-segment groups of "
                                                 f"facts_hourly, none only in the SQL, none only in the formula"))

    # ------------------------------------------------------------------------------------- split
    def _split(self, spec: MetricSpec, reading: Reading, grain: str) -> dict:
        """Is the query a function of sums? At the grain of the facts every group is one fact row, so an
        average, a maximum or a count of rows equals the formula there and nowhere else, and no comparison
        of values can tell. The query is run again on a private copy of the newest hours, as they are and
        with every fact row split in two: a function of sums returns the same values both times."""
        if not spec.sql:
            return _check("split", "skip", "no SQL to run again: the formula is computed from sums at every level")
        try:
            whole, halves, rows = self.store.rerun_on_split_rows(spec.sql, SPLIT_HOURS, timeout=self.sql_timeout,
                                                                max_rows=SQL_MAX_ROWS)
            sides = [pd.DataFrame({**{k: df[k].astype(str) for k in reading.keys},
                                   side: pd.to_numeric(df["value"], errors="coerce").astype(float)})
                     for side, df in (("whole", whole), ("halves", halves))]
            both = sides[0].merge(sides[1], on=reading.keys, how="outer")
        except Exception as e:   # it ran on the market a moment ago: whatever this is, the question stays open
            why = (str(e).strip().splitlines() or [type(e).__name__])[0][:200]
            return _check("split", "warn", f"could not be told whether the query is a function of sums: its rerun on a "
                                           f"copy of the last {SPLIT_HOURS} hours with every fact row split in two failed ({why})")
        w, h = both["whole"].to_numpy(float), both["halves"].to_numpy(float)
        valued, paired = np.isfinite(w) | np.isfinite(h), np.isfinite(w) & np.isfinite(h)
        with np.errstate(invalid="ignore"):
            changed = int((valued & ~(paired & _equal(w, h, SPLIT_TOL))).sum())
        compared = int(valued.sum())
        worst = rel_error(w[paired], h[paired])[0] if paired.any() else float("nan")
        where = f"each of the {rows} fact rows of the last {SPLIT_HOURS} hours"
        if not compared:
            return _check("split", "warn", f"could not be told whether the query is a function of sums: run again on a "
                                           f"private copy of {where}, it returns no value to compare")
        if changed:
            return _check("split", "warn", f"the query is not a function of sums: {changed} of {compared} values change "
                                           f"when {where} is split in two (max relative change {worst:.1e}); at grain "
                                           f"{grain} a group can be one fact row, so a query over rows equals "
                                           f"{spec.formula.text} there without being it")
        return _check("split", "pass", f"a function of sums: run again on a private copy with {where} split in two, none "
                                       f"of {compared} values changes (max relative change {worst:.1e})")

    # ------------------------------------------------------------------------------------ rollup
    def _rollup(self, spec: MetricSpec, trial: MetricGraph, evaluator: Evaluator, reading: Reading,
                fine: np.ndarray) -> dict:
        """Do the values in the segments combine into the value of a group, the way the breakdown links
        say: added up for an additive metric, weighted by the denominator for a ratio of two additive
        metrics? Computed for every dimension on the segments that have a value. A segment with a
        numerator and a zero denominator has no value of its own; such segments are counted."""
        name, f, dims = spec.name, spec.formula, reading.dims
        if not dims:
            return _check("rollup", "skip", "the metric has no dimensions to roll up over")
        weight, numerator = None, None
        if not trial.additive[name]:
            parts = f.ratio_parts() if f.kind == "ratio" else ([], [])
            if not (len(parts[0]) == 1 and len(parts[1]) == 1 and all(trial.additive[n] for n in parts[0] + parts[1])):
                return _check("rollup", "skip", f"{f.text} is neither a sum nor a ratio of two sums: for a group of segments "
                                                f"it is the formula on the group's totals, and there is no rule that "
                                                f"combines its segment values, so nothing was computed")
            numerator, weight = parts[0][0], parts[1][0]
        frame = reading.frame
        rows = ((frame["in_facts"] & frame["in_sql"]) if spec.sql else frame["in_facts"]).to_numpy(bool)
        valued = rows & np.isfinite(fine)
        base = sorted(evaluator.base_columns(name))
        work = frame.loc[valued, reading.keys + base].copy()
        work["_part"] = fine[valued]
        unvalued = 0
        if weight is not None:
            w = pd.to_numeric(evaluator.derive(work, [weight])[weight], errors="coerce").to_numpy(float)
            work["_part"] = work["_part"].to_numpy(float) * w
            work["_weight"] = w
            rest = frame.loc[rows & ~valued, reading.keys + base]
            if len(rest):
                top = pd.to_numeric(evaluator.derive(rest, [numerator])[numerator], errors="coerce").to_numpy(float)
                unvalued = int((np.abs(top) > 0).sum())
        compared, worst, off, where = 0, 0.0, 0, ""
        for d in dims:
            keys = [k for k in reading.keys if k != d]
            groups = work.groupby(keys, sort=False)
            totals = groups[base].sum(min_count=1)
            totals = totals.where(groups[base].count().eq(groups.size(), axis=0))     # never a partial sum
            expected = pd.to_numeric(evaluator.derive(totals.reset_index(), [name])[name], errors="coerce").to_numpy(float)
            rolled = groups["_part"].sum(min_count=1)
            if weight is not None:
                rolled = rolled / groups["_weight"].sum(min_count=1)
            rolled = rolled.replace([np.inf, -np.inf], np.nan).to_numpy(float)
            both = np.isfinite(rolled) & np.isfinite(expected)
            with np.errstate(invalid="ignore"):
                wrong = both & ~_equal(rolled, expected, max(spec.tolerance, 1e-9))
            err, n = rel_error(rolled[both], expected[both]) if both.any() else (0.0, 0)
            compared += n
            off += int(wrong.sum()) + int((np.isfinite(rolled) != np.isfinite(expected)).sum())
            if n and err >= worst:
                worst, where = err, d
        how = f"weighted by {weight}" if weight else "added up"
        note = (f"; {unvalued} segment-hours have {numerator} and no {weight}: they have no value of their own and count "
                f"only in a group's total") if unvalued else ""
        if off or not compared:
            return _check("rollup", "warn", f"does not roll up over {_words(dims)}: the segments' values, {how}, differ from "
                                            f"the formula on the totals in {off} of {compared} groups (max relative error "
                                            f"{worst:.1e}, over {where}){note}")
        basis = ("; this follows from the query being equal to the formula row by row, the query itself is tested by split"
                 if spec.sql else "; this holds by construction, the formula is computed from sums")
        return _check("rollup", "pass", f"rolls up over {_words(dims)}: the segments' values, {how}, give the formula on "
                                        f"the totals in all {compared} groups (max relative error {worst:.1e}){note}{basis}")

    # --------------------------------------------------------------------------------- placement
    def _place(self, base: MetricGraph, base_specs: list[MetricSpec], others: dict[str, Accepted], spec: MetricSpec,
               raw: dict, sig: dict, settled: bool) -> tuple[MetricSpec, list[dict], dict | None]:
        """Where the metric joins what is there. Returns the spec to register, every identity found with
        what was done with it, and the one that was applied (or None)."""
        name = spec.name
        found = identities(base, sig, name)
        # as a part of another metric the new one is a leaf: it keeps its formula as a definition
        leaf = replace(spec, atomic=True) if "atomic" not in raw and spec.decomposition is None else None
        under_outcome = set(base.specs) - set(base.outside_tree())
        outcomes = _words(base.top_outcomes())
        for c in found:
            parent = base.specs[c["parent"]]
            own = parent.investigative_formula
            if own is not None:
                c.update(status="alternative", reason=f"{parent.name} already decomposes as {own.text}")
            elif parent.atomic:
                c.update(status="alternative", reason=f"{parent.name} is declared atomic")
            elif not c["whole"]:
                c.update(status="alternative", reason=f"it makes {parent.name} what is left of another metric, not a "
                                                      f"whole made of parts")
            elif leaf is None:
                c.update(status="alternative", reason=f"the definition of {name} says how it decomposes itself")
            else:
                try:
                    MetricGraph.from_specs(self._specs({**others, "": Accepted(leaf, "validated", parent.name, c["decomposition"])}))
                except GraphError as e:
                    c.update(status="alternative", reason=str(e))
                    continue
                if not settled:
                    c.update(status="proposed", reason=f"{name} is tentative: a tentative metric does not change how "
                                                       f"another metric decomposes")
                elif parent.name in under_outcome:
                    c.update(status="proposed", reason=f"{parent.name} is under the outcome {outcomes}: how an outcome is "
                                                       f"decomposed is changed by a person, not by a contribution")
                else:
                    c.update(status="applicable", reason="")
        fitting = [c for c in found if c["status"] == "applicable"]
        applied = None
        if len(fitting) == 1:
            applied = fitting[0]
            applied.update(status="applied", reason=f"{applied['parent']} had no decomposition of its own")
        elif fitting:
            for c in fitting:
                c.update(status="ambiguous", reason=f"{len(fitting)} identities fit equally")
        if applied is not None:
            return leaf, found, applied
        if leaf is not None and any(w.metric == name for w in watch_list(MetricGraph.from_specs(base_specs + [leaf]))):
            return leaf, found, None     # a rate of two columns is atomic, as every such rate in the graph
        return spec, found, None

    @staticmethod
    def _inherit(base: MetricGraph, spec: MetricSpec, sig: dict[str, np.ndarray]) -> tuple[MetricSpec, dict]:
        """The context of the metrics the validated formula is built from. Their release systems join the
        metric's own (the graph draws a release link for each and the calendar link for a calendar system);
        their data sources reach it through the formula, so its freshness is theirs. A metric that equals a
        product of two or three watched rates for any data (orders / checkouts = payment_attempt_rate *
        payment_success_rate * order_rate, found by signature) takes their systems as well: a release that
        moves one of them moves it. Returns the spec with the systems and the check that says what came from where."""
        names = list(dict.fromkeys(spec.formula.names))
        own = list(dict.fromkeys(spec.systems))
        rates = [w.metric for w in watch_list(base) if w.metric in sig]
        products = [combo for k in (2, 3) for combo in itertools.combinations(rates, k)
                    if spec.name in sig and same(np.prod([sig[m] for m in combo], axis=0), sig[spec.name])]
        lenders = names + [m for combo in products for m in combo if m not in names]
        origin: dict[str, list[str]] = {}
        for n in dict.fromkeys(lenders):
            for system in base.specs[n].systems:
                origin.setdefault(system, [])
                if n not in origin[system]:
                    origin[system].append(n)
        inherited = [s for s in origin if s not in own]
        systems = tuple(own + inherited)
        sources: dict[str, list[str]] = {}
        for n in names:
            for src in base.sources(n):
                sources.setdefault(src, []).append(n)
        parts = []
        if inherited:
            by_metric: dict[str, list[str]] = {}
            for s in inherited:
                by_metric.setdefault(_words(origin[s]), []).append(s)
            parts.append("releases of " + "; ".join(f"{_words(s)} (from {m})" for m, s in by_metric.items()))
            calendar = [s for s in systems if s in CALENDAR_SYSTEMS]
            if calendar:
                parts.append(f"the promo and holiday calendar (through {_words(calendar)})")
        if sources:
            parts.append("freshness of " + "; ".join(f"{src} (from {_words(ms)})" for src, ms in sorted(sources.items())))
        whose, carry = _words(names), "carries" if len(names) == 1 else "carry"
        if products:
            whose += " and of the rates it equals the product of for any data (" + "; ".join(
                f"{spec.name} = {' * '.join(c)}" for c in products) + ")"
        if inherited:
            detail = f"it takes the context of {whose}: {'; '.join(parts)}"
        elif own:
            detail = f"{whose} {carry} no release system beyond the ones it declares, so no other release link is inherited"
        else:
            detail = f"{whose} {carry} no release system, so no release or calendar link is inherited"
        if sources and not inherited:
            detail += f"; it inherits the {parts[-1]}"
        if own:
            detail += f"; declared by the definition: {_words(own)}"
        return replace(spec, systems=systems), _check("context", "pass", detail)

    @staticmethod
    def _placement_check(found: list[dict], applied: dict | None, proven: str) -> dict:
        others = [c for c in found if c is not applied]
        kept = f"; {len(others)} more kept as alternatives or proposals" if others else ""
        if applied is not None:
            return _check("placement", "pass", f"1 identity places it: {applied['identity']}"
                                               + (f" ({proven})" if proven else "") + kept)
        fitting = [c for c in found if c["status"] == "ambiguous"]
        if fitting:
            return _check("placement", "warn", f"{len(fitting)} identities fit equally and none was applied: "
                                               + "; ".join(c["identity"] for c in fitting))
        if not found:
            return _check("placement", "pass", "no identity makes it a part of an existing metric: it stands by its own formula")
        return _check("placement", "pass", f"it stands by its own formula; {len(found)} identities that would make it a part "
                                           f"of an existing metric hold and are kept, not applied: "
                                           + "; ".join(f"{c['identity']} ({c['status']})" for c in found[:4]))

    def _links(self, name: str, found: list[dict]) -> list[dict]:
        """The links the metric has in the graph now, and the identities that were not applied."""
        g, links = self.graph, []
        applied = next((c for c in found if c["status"] == "applied"), None)
        for e in g.out_edges(name) + g.in_edges(name):
            link = {"kind": e.kind, "source": e.src, "target": e.dst, "status": g.status.get(e.id) or "added"}
            link.update({k: e.attrs[k] for k in ("role", "dimension", "via") if e.attrs.get(k) is not None})
            if applied is not None and e.kind == "formula" and e.src == applied["parent"]:
                link.update(identity=applied["identity"], applied=True)
            links.append(link)
        if applied is not None:
            for e in g.out_edges(applied["parent"], "formula"):
                if e.dst != name:
                    links.append({"kind": "formula", "source": e.src, "target": e.dst, "role": e.attrs.get("role"),
                                  "status": g.status.get(e.id) or "added", "identity": applied["identity"], "applied": True})
        for c in found:
            if c is not applied:
                links.append({"kind": "formula", "source": c["parent"], "target": name, "identity": c["identity"],
                              "status": c["status"], "applied": False, "reason": c["reason"]})
        return links

    def _placed_sentence(self, final: MetricSpec, found: list[dict], applied: dict | None) -> str:
        name, g = final.name, self.graph
        kind = "formula" if final.investigative_formula is not None else "definition"
        outcomes = _words(g.top_outcomes())
        beside = f"outside the tree of the outcome {outcomes}" if outcomes else "in a tree of its own"
        parts = []
        if applied is not None:
            parent = applied["parent"]
            reach = f"{parent} is under the outcome {outcomes}" if parent not in g.outside_tree() else \
                f"{parent} is not under the outcome {outcomes or '(none is declared)'}, so the two are drawn as a tree of their own"
            parts.append(f"{name} is {applied['role']} of {parent} ({applied['identity']}); {reach}")
        else:
            fitting = [c for c in found if c["status"] == "ambiguous"]
            if fitting:
                parts.append(f"the place of {name} is ambiguous: {len(fitting)} identities fit equally "
                             f"({'; '.join(c['identity'] for c in fitting)}) and none was applied, so it stands by its "
                             f"own formula {final.formula.text}")
            else:
                parts.append(f"{name} stands by its own formula {final.formula.text}, {beside}: no metric without a "
                             f"decomposition is made of it")
        for c in found:
            if c["status"] == "proposed":
                parts.append(f"proposed, not applied: {c['identity']} ({c['reason']})")
        parts.append(f"it is linked to {_words(final.formula.names)} by {kind}"
                     + (f", breaks down by {_words(final.dimensions)}" if final.dimensions else "")
                     + (f" and is as fresh as the source {_words(g.sources(name))}" if g.sources(name) else ""))
        if final.systems:
            calendar = any(s in CALENDAR_SYSTEMS for s in final.systems)
            parts.append(f"it is linked to the releases of {_words(final.systems)}"
                         + (" and to the promo and holiday calendar" if calendar else ""))
        return "; ".join(parts) + "."

    @staticmethod
    def _message(name: str, status: str, spec: MetricSpec, source: str, stats: dict, reading: Reading,
                 checks: list[dict], found: list[dict], applied: dict | None, watch: str,
                 fits: int = 1, tried: int = 0) -> str:
        f = spec.formula.text
        if not spec.sql:
            evidence = f"formula {f}, no SQL to compare"
        elif source == "inferred":
            among = (f"the only one of {tried} candidates" if fits == 1 else
                     f"the first of {fits} of {tried} candidates that fit")
            evidence = f"formula {f} found from the SQL, {among}, equal on {stats['compared']} rows"
        else:
            evidence = f"the SQL equals {f} on {stats['compared']} rows"
        if status == "validated":
            lead = f"{name} added to the graph and validated on {reading.span} of data ({evidence})"
        else:
            status_of = {c["check"]: c["status"] for c in checks}
            caveats = []
            if status_of.get("rows") == "warn":
                caveats.append(f"the SQL returns only part of the rows ({stats['sql']} of {stats['facts']})")
            if status_of.get("formula") == "warn":
                caveats.append("more than one formula fits the SQL")
            if status_of.get("split") == "warn":
                detail = next(c["detail"] for c in checks if c["check"] == "split")
                caveats.append("the SQL is not a function of sums: its values change when the fact rows are split in two"
                               if detail.startswith("the query is not") else
                               "the SQL could not be run again on split fact rows, so it is not known to be a function of sums")
            if status_of.get("rollup") == "warn":
                caveats.append("its values in the segments do not roll up to the formula on the totals")
            if any(c["status"] == "ambiguous" for c in found):
                caveats.append("its place in the graph is ambiguous")
            lead = f"{name} added to the graph as tentative: {'; '.join(caveats)} (checked on {reading.span} of data: {evidence})"
        if applied is not None:
            place = f"placed as {applied['role']} of {applied['parent']} ({applied['identity']})"
        elif any(c["status"] == "ambiguous" for c in found):
            place = "not placed under another metric"
        else:
            place = f"linked to {_words(spec.formula.names)}"
        return "; ".join(p for p in (lead, place, watch) if p)
