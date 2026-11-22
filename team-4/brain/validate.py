"""Validation of the graph against the data, run when the graph is loaded.

Checks per metric:
  coverage     the metric has finite values in the window; a declared column that is missing
               from the data is a failure (broken contract), not a skip
  identity     its investigative formula reproduces it, hourly, in total and per segment
               (skipped only when the formula *is* its sole definition: true by construction)
  definition   a metric with a column or SQL *and* a formula: the two agree, at the SQL's grain;
               the SQL must group by exactly the metric's declared dimensions
  additivity   an additive metric's segments sum to its total, for every dimension
  bounds       hourly values stay inside the declared range
Graph-wide (status "warn"): release systems and data sources in the database that no metric
is linked to, and linked sources with no freshness records.

Every check has a basis. "definition": it follows from the metric definitions alone and no data
can make it fail (orders = sessions * (orders / sessions); sums of columns of one table add up) -
it proves the YAML is consistent, nothing about the business. "data": it can fail on the data
(coverage, bounds, a contributed SQL against its formula, an identity between independent columns).
The report keeps the two apart so that "validated" is not read as more than it is.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from brain.data import TS_FORMAT, Store, iso_hour
from brain.evaluator import DataMissing, Evaluator, NotComputable
from brain.graph import MetricGraph
from brain.metric_spec import DIMENSIONS
from brain.signature import holds_by_construction, signatures

EPS = 1e-9
MIN_COVERAGE = 0.95  # share of the window's hours that must have a value


@dataclass
class Check:
    metric: str
    check: str
    status: str  # pass | fail | skip | warn
    detail: str
    worst: float | None = None
    basis: str = "data"  # data | definition


def rel_error(a, b) -> tuple[float, int]:
    """Largest relative difference over rows where both are finite, and how many rows were compared."""
    a = pd.to_numeric(pd.Series(a), errors="coerce").to_numpy(dtype=float)
    b = pd.to_numeric(pd.Series(b), errors="coerce").to_numpy(dtype=float)
    mask = np.isfinite(a) & np.isfinite(b)
    if not mask.any():
        return float("nan"), 0
    denom = np.maximum(np.maximum(np.abs(a[mask]), np.abs(b[mask])), EPS)
    return float(np.max(np.abs(a[mask] - b[mask]) / denom)), int(mask.sum())


def _sql_definition(m, s, ev: Evaluator, store: Store, start, end, computable: set[str]) -> Check:
    try:
        df = store.read_sql(s.sql)
    except PermissionError as e:
        return Check(m, "definition", "fail", f"SQL refused: {e}")
    except Exception as e:  # a contributed query that does not run is a failed definition
        return Check(m, "definition", "fail", f"SQL failed: {str(e).splitlines()[0][:200]}")
    cols = list(df.columns)
    if "ts" not in cols or "value" not in cols:
        return Check(m, "definition", "fail", f"SQL must return ts and value; it returns {cols}")
    dims = [c for c in cols if c in DIMENSIONS]
    extra = [c for c in cols if c not in dims + ["ts", "value"]]
    if extra:
        return Check(m, "definition", "fail", f"SQL returns unexpected columns {extra}")
    if set(dims) != set(s.dimensions):
        return Check(m, "definition", "fail",
                     f"SQL groups by {dims} but the metric declares dimensions {list(s.dimensions)}")
    df = df[(df["ts"].astype(str) >= iso_hour(start)) & (df["ts"].astype(str) < iso_hour(end))]
    if df.empty:
        return Check(m, "definition", "fail", "SQL returns no rows in the validation window")
    if s.formula is None:
        return Check(m, "definition", "skip", "SQL only; the extension agent has to infer a formula")
    if m not in computable:
        return Check(m, "definition", "fail", f"formula {s.formula.text} cannot be computed from the data")
    f = ev.frame([m], start, end, group_by=tuple(dims), by_hour=True)
    f["ts"] = f["ts"].dt.strftime(TS_FORMAT)
    merged = df.astype({"ts": str}).merge(f, on=["ts"] + dims, how="inner")
    err, n = rel_error(merged["value"], merged[m])
    ok = n > 0 and err <= s.tolerance
    return Check(m, "definition", "pass" if ok else "fail",
                 f"SQL vs {s.formula.text}: max relative error {err:.2e} over {n} rows at grain {['ts'] + dims}", err)


def validate_graph(graph: MetricGraph, store: Store, days: int = 7, start=None, end=None) -> list[Check]:
    ev = Evaluator(graph, store)
    if end is None:
        end = store.time_range()[1] + pd.Timedelta(hours=1)
    if start is None:
        start = pd.Timestamp(end) - pd.Timedelta(days=days)

    checks: list[Check] = []
    computable: list[str] = []
    for m, s in graph.specs.items():
        try:
            ev.base_columns(m)
            computable.append(m)
        except DataMissing as e:
            checks.append(Check(m, "coverage", "fail", str(e)))
        except NotComputable as e:
            if not s.sql:
                checks.append(Check(m, "coverage", "fail", str(e)))
    comp = set(computable)
    sig = signatures(graph)

    total = ev.frame(computable, start, end, by_hour=True)
    per_dim = {d: ev.frame(computable, start, end, group_by=(d,), by_hour=True) for d in DIMENSIONS}
    levels = [("total", total)] + [(f"by {d}", f) for d, f in per_dim.items()]

    for m, s in graph.specs.items():
        if s.sql:
            checks.append(_sql_definition(m, s, ev, store, start, end, comp))
        if m not in comp:
            continue

        # coverage
        finite = int(np.isfinite(pd.to_numeric(total[m], errors="coerce")).sum())
        enough = len(total) > 0 and finite >= MIN_COVERAGE * len(total)
        checks.append(Check(m, "coverage", "pass" if enough else "fail",
                            f"{finite} of {len(total)} hours have a value"
                            + ("" if enough else f" (at least {MIN_COVERAGE:.0%} required)")))

        # identity along the investigative formula
        inv = s.investigative_formula
        if inv is not None:
            missing = [n for n in inv.names if n not in comp]
            if missing:
                checks.append(Check(m, "identity", "fail", f"{inv.text}: cannot compute {missing}"))
                for e in graph.out_edges(m, "formula"):
                    graph.status[e.id] = "failed"
            elif s.formula is not None and inv.text == s.formula.text and not s.column and not s.sql:
                checks.append(Check(m, "identity", "skip", f"{inv.text} is its definition"))
            else:
                worst, compared, where = 0.0, 0, ""
                for label, f in levels:
                    expr = inv.evaluate({n: f[n] for n in inv.names})
                    err, n = rel_error(f[m], expr)
                    compared += n
                    if n and err > worst:
                        worst, where = err, label
                ok = compared > 0 and worst <= s.tolerance
                by_construction = holds_by_construction(graph, m, sig)
                checks.append(Check(m, "identity", "pass" if ok else "fail",
                                    f"{m} = {inv.text}: max relative error {worst:.2e}"
                                    + (f" ({where})" if where else "") + f" over {compared} rows"
                                    + ("; holds by construction" if by_construction else ""),
                                    worst, basis="definition" if by_construction else "data"))
                for e in graph.out_edges(m, "formula"):
                    graph.status[e.id] = "validated" if ok else "failed"

        # column vs formula
        if s.formula is not None and s.column and (inv is None or inv.text != s.formula.text):
            expr = s.formula.evaluate({n: total[n] for n in s.formula.names})
            err, n = rel_error(total[m], expr)
            ok = n > 0 and err <= s.tolerance
            checks.append(Check(m, "definition", "pass" if ok else "fail",
                                f"column {s.column} vs {s.formula.text}: max relative error {err:.2e}", err))

        # additivity
        if graph.additive.get(m):
            if not s.dimensions:
                checks.append(Check(m, "additivity", "skip", "no dimensions declared"))
            else:
                worst, bad_dim, compared = 0.0, "", 0
                for d in s.dimensions:
                    summed = per_dim[d].groupby("ts")[m].sum(min_count=1)
                    aligned = total.set_index("ts")[m].reindex(summed.index)
                    err, n = rel_error(aligned, summed)
                    compared += n
                    if n and err > worst:
                        worst, bad_dim = err, d
                ok = compared > 0 and worst <= max(s.tolerance, 1e-9)
                checks.append(Check(m, "additivity", "pass" if ok else "fail",
                                    "segments sum to the total for " + ", ".join(s.dimensions) if ok else
                                    (f"segments by {bad_dim} differ from the total by {worst:.2e}" if compared
                                     else "no rows to compare"), worst, basis="definition"))

        # bounds
        if s.bounds is not None:
            lo, hi = s.bounds
            n_bad, n_all = 0, 0
            for _, f in levels:
                v = pd.to_numeric(f[m], errors="coerce")
                v = v[np.isfinite(v)]
                n_all += len(v)
                if lo is not None:
                    n_bad += int((v < lo - 1e-12).sum())
                if hi is not None:
                    n_bad += int((v > hi + 1e-12).sum())
            checks.append(Check(m, "bounds", "pass" if n_bad == 0 else "fail",
                                f"{n_bad} of {n_all} hourly values outside [{lo}, {hi}]"))

    checks.extend(_context_checks(graph, store))

    failed = {c.metric for c in checks if c.status == "fail"}
    for m in graph.specs:
        if m in failed:
            graph.status[m] = "failed"
        elif m in comp:
            graph.status[m] = "validated"
        else:
            graph.status[m] = "unvalidated"
    return checks


def _context_checks(graph: MetricGraph, store: Store) -> list[Check]:
    out: list[Check] = []
    linked_systems = {sys_ for s in graph.specs.values() for sys_ in s.systems}
    linked_sources = {src for m in graph.specs for src in graph.sources(m)}
    db_systems = {v for v in store.distinct("releases", "system") if v}
    db_sources = {v for v in store.distinct("ingestion_log", "source") if v}
    if db_systems - linked_systems:
        out.append(Check("*", "context", "warn",
                         f"releases for systems no metric is linked to: {sorted(db_systems - linked_systems)}"))
    if db_sources - linked_sources:
        out.append(Check("*", "context", "warn",
                         f"ingestion sources no metric depends on: {sorted(db_sources - linked_sources)}"))
    if db_sources and linked_sources - db_sources:
        out.append(Check("*", "context", "warn",
                         f"metrics depend on sources with no freshness records: {sorted(linked_sources - db_sources)}"))
    return out


def summarize(checks: list[Check]) -> str:
    by_status = {s: [c for c in checks if c.status == s] for s in ("pass", "fail", "skip", "warn")}
    metrics = sorted({c.metric for c in checks if c.metric != "*"})
    on_data = [c for c in by_status["pass"] if c.basis == "data"]
    by_definition = [c for c in by_status["pass"] if c.basis == "definition"]
    lines = [f"Validation: {len(on_data)} checks on data passed, {len(by_status['fail'])} failed, "
             f"{len(by_status['skip'])} skipped, {len(by_status['warn'])} warnings across {len(metrics)} metrics; "
             f"{len(by_definition)} more hold by construction (consistent definitions, not evidence from data)"]
    for c in by_status["fail"]:
        lines.append(f"  ✗ {c.metric} [{c.check}] {c.detail}")
    for c in by_status["warn"]:
        lines.append(f"  ! [{c.check}] {c.detail}")
    for c in by_status["skip"]:
        if c.check != "identity":
            lines.append(f"  – {c.metric} [{c.check}] {c.detail}")
    return "\n".join(lines)
