"""Finding candidate connections for a new metric (L3, Person A).

A contributor gives only a metric definition (name, description, unit,
calculation, dimensions). ``discover_connections`` proposes where it belongs
in the graph, using four methods:

1. **Shared columns and dimensions.** Which existing metrics read the same
   table columns and can be sliced the same way. This ranks related metrics
   and orders the formula search.
2. **Formula search.** Tries simple arithmetic between the new metric and
   existing ones on every hour of data: ``N = A * B``, ``N = A / B``,
   ``N = A + B (+ C)``, ``N = A - B``, ``N = 1 - A``. A formula that fits is
   then re-checked on every segment, so a coincidence on the totals can't
   pass.
3. **Lagged correlation.** Correlates the metric with every other metric
   after removing daily and weekly seasonality, at lags up to ``max_lag``
   hours. Strong correlations become ``possible_cause`` candidates: a
   hypothesis, not a fact.
4. **Breakdowns.** One candidate per declared dimension.

Every candidate is a ``GraphEdge`` with ``status="uncertain"`` and
``source="auto_discovered"``, plus the score and evidence behind it.
Validating and adding them to the graph is L3 Person B's step.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd

from graph.edges import GraphEdge
from graph.engine import MetricEngine, ValidationReport
from graph.metric_graph import MetricGraph
from graph.registry import MetricDefinition

FORMULA_TOL = 1e-6  # max relative error for an arithmetic identity
MIN_VALID_SHARE = 0.8  # share of hours that must be comparable
MIN_CORRELATION = 0.3
MAX_CORRELATION_CANDIDATES = 5
LEAD_MARGIN = 0.05  # a lag beats lag 0 only if |r| is this much higher

_ADDITIVE_UNITS = {"count", "currency_eur"}


@dataclass
class Candidate:
    """A proposed connection for the new metric."""

    edge: GraphEdge
    method: str  # "formula", "breakdown" or "correlation"
    score: float  # strength of the discovery evidence, 0..1
    evidence: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        e = self.edge
        what = e.formula or (f"by {e.dimension}" if e.type == "breakdown" else f"{e.from_metric} -> {e.to_metric}")
        lag = f", lag {e.lag_hours}h" if e.lag_hours else ""
        return f"{e.type:14} {what}{lag}  (score {self.score:.2f})"


@dataclass
class DiscoveryReport:
    metric: MetricDefinition
    validation: ValidationReport
    candidates: list[Candidate] = field(default_factory=list)
    related_metrics: list[tuple[str, float]] = field(default_factory=list)  # (name, column overlap)
    duplicates: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def by_method(self, method: str) -> list[Candidate]:
        return [c for c in self.candidates if c.method == method]

    def edges(self) -> list[GraphEdge]:
        return [c.edge for c in self.candidates]

    def best_formula(self) -> Optional[Candidate]:
        f = self.by_method("formula")
        return f[0] if f else None


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def discover_connections(
    metric: MetricDefinition | dict[str, Any],
    engine: MetricEngine,
    graph: Optional[MetricGraph] = None,
    start: Any = None,
    end: Any = None,
    max_lag: int = 6,
) -> DiscoveryReport:
    """Propose graph connections for ``metric`` (which need not be registered)."""
    defn = metric if isinstance(metric, MetricDefinition) else MetricDefinition.from_dict(metric)
    report = DiscoveryReport(defn, engine.validate(defn))
    if not report.validation.ok:
        report.notes.append("definition does not work on the data; no connections searched")
        return report

    pool = [m for m in engine.registry if m.name != defn.name and m.source_table == defn.source_table]
    report.duplicates = [m.name for m in pool if _norm(m.calculation) == _norm(defn.calculation)]
    if report.duplicates:
        report.notes.append(f"same calculation as existing metric(s) {report.duplicates}")
    pool = [m for m in pool if m.name not in report.duplicates]

    cols = engine.table_columns(defn.source_table)
    report.related_metrics = related_by_columns(defn, pool, cols)

    report.candidates += breakdown_candidates(defn)
    formulas = formula_candidates(defn, pool, engine, start, end, related=report.related_metrics)
    report.candidates += formulas
    in_formula = {n for c in formulas for n in c.details.get("inputs", [])} | {c.edge.to_metric for c in formulas}
    in_formula.discard(defn.name)
    structural = in_formula | formula_neighbours(graph, in_formula | {defn.name})
    if structural:
        report.notes.append(
            f"correlation search skips {len(structural)} metrics already linked by formulas"
        )
    report.candidates += correlation_candidates(
        defn, [m for m in pool if m.name not in structural], engine, start, end, max_lag=max_lag
    )

    if graph is not None:
        known = set(graph.graph.nodes)
        for c in report.candidates:
            others = {c.edge.from_metric, c.edge.to_metric} - {defn.name}
            missing = sorted(o for o in others if o not in known)
            if missing:
                c.details["not_in_graph"] = missing
    if not formulas:
        report.notes.append("no arithmetic formula found; the metric may only have possible causes")
    return report


def formula_neighbours(graph: Optional[MetricGraph], metrics: set[str]) -> set[str]:
    """Metrics connected to ``metrics`` through formula edges, in either direction.

    Their co-movement with the new metric is already explained by arithmetic,
    so a correlation with them is not evidence of a separate cause.
    """
    if graph is None:
        return set()
    import networkx as nx

    g = nx.DiGraph()
    for e in graph.edges("formula"):
        g.add_edge(e.from_metric, e.to_metric)
    out: set[str] = set()
    for m in metrics:
        if m in g:
            out |= nx.ancestors(g, m) | nx.descendants(g, m)
    return out - metrics


# ---------------------------------------------------------------------------
# 1. shared columns
# ---------------------------------------------------------------------------

def related_by_columns(
    defn: MetricDefinition, pool: list[MetricDefinition], columns: list[str]
) -> list[tuple[str, float]]:
    """Existing metrics ranked by overlap (Jaccard) of the columns they read."""
    mine = set(defn.columns_used(columns))
    out = []
    for m in pool:
        theirs = set(m.columns_used(columns))
        if mine and theirs and set(defn.dimensions) & set(m.dimensions):
            j = len(mine & theirs) / len(mine | theirs)
            if j > 0:
                out.append((m.name, round(j, 3)))
    return sorted(out, key=lambda t: (-t[1], t[0]))


# ---------------------------------------------------------------------------
# 4. breakdowns
# ---------------------------------------------------------------------------

def breakdown_candidates(defn: MetricDefinition) -> list[Candidate]:
    out = []
    for dim in defn.dimensions:
        edge = GraphEdge(defn.name, defn.name, "breakdown", dimension=dim, confidence=0.9,
                         source="auto_discovered", status="uncertain",
                         evidence=f"declared dimension {dim!r}")
        how = "segments should add up" if defn.is_additive else "segments recombine weighted by the denominator"
        out.append(Candidate(edge, "breakdown", 0.9, f"declared dimension; {how}",
                             {"additive": defn.is_additive}))
    return out


# ---------------------------------------------------------------------------
# 2. formula search
# ---------------------------------------------------------------------------

@dataclass
class _Fit:
    op: str
    terms: tuple[str, ...]
    error: float
    valid_share: float


def formula_candidates(
    defn: MetricDefinition,
    pool: list[MetricDefinition],
    engine: MetricEngine,
    start: Any = None,
    end: Any = None,
    related: Optional[list[tuple[str, float]]] = None,
    max_candidates: int = 5,
) -> list[Candidate]:
    """Arithmetic identities between the new metric and existing metrics."""
    if not pool:
        return []
    names = [m.name for m in pool]
    wide = engine.compute_many([defn] + pool, start, end, grain="hour")
    y = wide[defn.name].to_numpy(dtype=float)
    X = wide[names].to_numpy(dtype=float)  # (hours, metrics)
    defs = {m.name: m for m in pool}

    fits = _search(y, X, names, defn, defs)
    if not fits:
        return []
    rank = {n: s for n, s in (related or [])}
    columns = engine.table_columns(defn.source_table)
    mine = set(defn.columns_used(columns))

    def coverage(f: _Fit) -> float:
        """How well the inputs' columns match the new metric's columns (1 = exactly).
        A metric's own definition (product_clicks = search_clicks + ...) beats a
        circular one (product_clicks = cart_adds / add_to_cart_rate)."""
        theirs = set().union(*(defs[t].columns_used(columns) for t in f.terms))
        return len(mine & theirs) / len(mine | theirs) if mine | theirs else 0.0

    fits.sort(key=lambda f: (-round(coverage(f), 3), _complexity(f), -sum(rank.get(t, 0) for t in f.terms), f.error))

    out = []
    for fit in fits[: max_candidates * 3]:
        seg = _segment_check(defn, fit, defs, engine, start, end)
        if seg["status"] == "fail":
            continue
        formula, lhs, inputs = _render(defn, fit, defs)
        cov = coverage(fit)
        # verified on segments, matching the metric's own columns, few terms
        score = (0.95 if seg["status"] == "pass" else 0.8) - 0.3 * (1 - cov) - 0.02 * (_complexity(fit) - 1)
        evidence = (f"holds on {fit.valid_share:.0%} of hours (max error {fit.error:.1e})"
                    + (f"; also on {seg['rows']} segment-days by {seg['by']}" if seg["status"] == "pass"
                       else f"; {seg['reason']}"))
        frm = defn.name if defn.name in inputs else inputs[0]
        edge = GraphEdge(frm, lhs, "formula", formula=formula, confidence=round(score, 2),
                         source="auto_discovered", status="uncertain", evidence=evidence)
        out.append(Candidate(edge, "formula", round(score, 2), evidence,
                             {"op": fit.op, "terms": list(fit.terms), "inputs": inputs,
                              "max_error": fit.error, "column_coverage": round(cov, 3),
                              "segment_check": seg}))
        if len(out) >= max_candidates:
            break
    if len(out) > 1:
        for c in out[1:]:
            c.details["alternative_to"] = out[0].edge.formula
    return out


def _search(y, X, names, defn, defs) -> list[_Fit]:
    fits: list[_Fit] = []
    n = len(names)
    additive = [i for i, nm in enumerate(names) if defs[nm].is_additive]

    def check(op: str, terms: tuple[str, ...], pred: np.ndarray) -> None:
        err, share = _fit_error(y, pred)
        if share >= MIN_VALID_SHARE and err <= FORMULA_TOL:
            fits.append(_Fit(op, terms, err, share))

    with np.errstate(divide="ignore", invalid="ignore"):
        for i in range(n):
            check("equal", (names[i],), X[:, i])
            check("complement", (names[i],), 1.0 - X[:, i])
            for j in range(n):
                if i == j:
                    continue
                if i < j:
                    check("product", (names[i], names[j]), X[:, i] * X[:, j])
                check("ratio", (names[i], names[j]), X[:, i] / X[:, j])
                check("difference", (names[i], names[j]), X[:, i] - X[:, j])
        if defn.unit in _ADDITIVE_UNITS:
            for i, j in itertools.combinations(additive, 2):
                check("sum", (names[i], names[j]), X[:, i] + X[:, j])
            for i, j, k in itertools.combinations(additive, 3):
                check("sum", (names[i], names[j], names[k]), X[:, i] + X[:, j] + X[:, k])
    # an "equal" fit means the metric is a duplicate under another name
    return [f for f in fits if f.op != "equal"] or fits


def _fit_error(y: np.ndarray, pred: np.ndarray) -> tuple[float, float]:
    ok_y = np.isfinite(y)
    valid = ok_y & np.isfinite(pred)
    if not ok_y.any():
        return np.inf, 0.0
    share = valid.sum() / ok_y.sum()
    if not valid.any():
        return np.inf, 0.0
    denom = np.maximum(np.abs(y[valid]), 1e-12)
    return float(np.max(np.abs(y[valid] - pred[valid]) / denom)), float(share)


def _complexity(f: _Fit) -> int:
    return {"product": 1, "ratio": 1, "complement": 1, "equal": 0, "difference": 2}.get(f.op, len(f.terms))


def _segment_check(defn, fit, defs, engine, start, end) -> dict[str, Any]:
    """Re-check a fit per segment and day. An identity holds in every slice."""
    dims = [d for d in defn.dimensions if all(d in defs[t].dimensions for t in fit.terms)]
    if not dims:
        return {"status": "skipped", "reason": "no shared dimension to re-check segments", "by": [], "rows": 0}
    wide = engine.compute_many([defn] + [defs[t] for t in fit.terms], start, end, by=dims, grain="day")
    y = wide[defn.name].to_numpy(dtype=float)
    cols = [wide[t].to_numpy(dtype=float) for t in fit.terms]
    with np.errstate(divide="ignore", invalid="ignore"):
        pred = {
            "product": lambda: cols[0] * cols[1],
            "ratio": lambda: cols[0] / cols[1],
            "difference": lambda: cols[0] - cols[1],
            "complement": lambda: 1.0 - cols[0],
            "equal": lambda: cols[0],
            "sum": lambda: np.sum(cols, axis=0),
        }[fit.op]()
    err, share = _fit_error(y, pred)
    rows = int(np.isfinite(y).sum())
    if share >= MIN_VALID_SHARE and err <= FORMULA_TOL:
        return {"status": "pass", "by": dims, "rows": rows, "max_error": err}
    return {"status": "fail", "by": dims, "rows": rows, "max_error": err,
            "reason": f"breaks on segments by {dims} (max error {err:.2g})"}


def _render(defn: MetricDefinition, fit: _Fit, defs) -> tuple[str, str, list[str]]:
    """Formula text in the graph's convention, its left side, and its inputs.

    A count/count ratio (refund_rate = refunds / orders) is written the way the
    seed graph writes rates, as a driver of the count:
    ``refunds = orders * refund_rate``.
    """
    n, t = defn.name, fit.terms
    if fit.op == "ratio" and defs[t[0]].is_additive and defs[t[1]].is_additive:
        return f"{t[0]} = {t[1]} * {n}", t[0], sorted([t[1], n])
    if fit.op == "product":
        rhs = f"{t[0]} * {t[1]}"
    elif fit.op == "ratio":
        rhs = f"{t[0]} / {t[1]}"
    elif fit.op == "difference":
        rhs = f"{t[0]} - {t[1]}"
    elif fit.op == "complement":
        rhs = f"1 - {t[0]}"
    elif fit.op == "sum":
        rhs = " + ".join(t)
    else:  # equal
        rhs = t[0]
    return f"{n} = {rhs}", n, sorted(t)


def _norm(calc: str) -> str:
    return re.sub(r"\s+", "", calc).lower()


# ---------------------------------------------------------------------------
# 3. lagged correlation
# ---------------------------------------------------------------------------

def correlation_candidates(
    defn: MetricDefinition,
    pool: list[MetricDefinition],
    engine: MetricEngine,
    start: Any = None,
    end: Any = None,
    max_lag: int = 6,
    min_corr: float = MIN_CORRELATION,
    top_k: int = MAX_CORRELATION_CANDIDATES,
) -> list[Candidate]:
    """``possible_cause`` candidates from seasonality-adjusted, lagged correlation.

    Series are hourly per segment (the dimensions every metric shares). Each
    series is turned into a residual: log value minus its mean for the same
    segment, weekday and hour, which removes daily and weekly cycles.
    ``corr(new_t, other_{t-lag})`` is pooled over segments for
    lag = -max_lag..max_lag. A positive lag means the other metric moves first.
    """
    if not pool:
        return []
    preferred = [d for d in defn.dimensions if d != "category"][:2] or list(defn.dimensions[:1])
    groups: dict[tuple[str, ...], list[MetricDefinition]] = {}
    for m in pool:  # slice each metric as finely as both metrics allow
        groups.setdefault(tuple(d for d in preferred if d in m.dimensions), []).append(m)

    out = []
    for by_t, members in groups.items():
        out += _correlate_group(defn, members, list(by_t), engine, start, end, max_lag, min_corr)
    out.sort(key=lambda c: -abs(c.details["r"]))
    return out[:top_k]


def _correlate_group(defn, members, by, engine, start, end, max_lag, min_corr) -> list[Candidate]:
    wide = engine.compute_many([defn] + members, start, end, by=by, grain="hour")
    names = [defn.name] + [m.name for m in members]
    resid = _residuals(wide, names, by)

    out = []
    y = resid[defn.name]
    for m in members:
        lag, r, r0 = _best_lag(y, resid[m.name], resid["__seg"], max_lag)
        if r is None or abs(r) < min_corr or abs(r) > 0.995:
            continue
        if lag > 0:
            frm, to, direction = m.name, defn.name, f"{m.name} moves {lag}h before {defn.name}"
        elif lag < 0:
            frm, to, direction = defn.name, m.name, f"{defn.name} moves {-lag}h before {m.name}"
        else:
            frm, to, direction = m.name, defn.name, "same hour; direction unclear"
        score = round(min(0.85, abs(r)), 2)
        evidence = (f"seasonality-adjusted correlation r={r:+.2f} at lag {abs(lag)}h "
                    f"(r={r0:+.2f} at lag 0) over {by or 'total'}; {direction}")
        edge = GraphEdge(frm, to, "possible_cause", confidence=score, lag_hours=abs(lag),
                         source="auto_discovered", status="uncertain", evidence=evidence)
        out.append(Candidate(edge, "correlation", score, evidence,
                             {"r": r, "r_lag0": r0, "lag": lag, "segments_by": by,
                              "direction": "ambiguous" if lag == 0 else "lead"}))
    return out


def _residuals(wide: pd.DataFrame, names: list[str], by: list[str]) -> pd.DataFrame:
    df = wide.sort_values(by + ["period"]).reset_index(drop=True)
    seg = df[by].astype(str).agg("|".join, axis=1) if by else pd.Series("all", index=df.index)
    how = df["period"].dt.dayofweek * 24 + df["period"].dt.hour
    out = pd.DataFrame({"__seg": seg})
    for n in names:
        v = df[n].astype(float)
        pos = v[v > 0]
        floor = pos.min() * 0.5 if len(pos) else 1.0
        lv = np.log(v.clip(lower=floor))
        lv[v.isna()] = np.nan
        out[n] = lv - lv.groupby([seg, how]).transform("mean")
    return out


def _best_lag(y: pd.Series, x: pd.Series, seg: pd.Series, max_lag: int):
    best = (0, None)
    r0 = None
    for lag in range(-max_lag, max_lag + 1):
        xs = x.groupby(seg).shift(lag)  # value of x `lag` hours earlier (same segment)
        m = y.notna() & xs.notna()
        if m.sum() < 30 or y[m].std() == 0 or xs[m].std() == 0:
            continue
        r = float(np.corrcoef(y[m], xs[m])[0, 1])
        if lag == 0:
            r0 = r
        if best[1] is None or abs(r) > abs(best[1]):
            best = (lag, r)
    lag, r = best
    if r is None or r0 is None:
        return 0, r, r0
    # A lead must clearly beat lag 0, and must not sit at the edge of the search:
    # a best lag at +/-max_lag usually means a slow, persistent shift, not a lead.
    if lag != 0 and (abs(r) < abs(r0) + LEAD_MARGIN or abs(lag) == max_lag):
        return 0, r0, r0
    return lag, r, r0
