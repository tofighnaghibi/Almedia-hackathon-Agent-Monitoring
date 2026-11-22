"""MetricSemantics: pure checks for metric formulas (v3.2 §1.1, §3.1, §3.3 step 4).

Unit algebra, population and grain compatibility, structural-edge derivation and the numerical
identity check. Arithmetic is never causality: everything here produces structural facts only.
No I/O, no graph access. Callers pass the parent definitions in.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..contracts.variables import FormulaBinOp, FormulaConst, FormulaRef, MetricDefinition, formula_refs

_UNIT_TOKEN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\^(-?\d+))?$")
DIMENSIONLESS = frozenset()


class UnitError(ValueError):
    pass


def parse_unit(unit: str | None) -> frozenset[tuple[str, int]]:
    """'orders/order' -> {('orders',1),('order',-1)}; '1' or '' -> dimensionless. Symbols match exactly."""
    if unit is None:
        raise UnitError("unit not declared")
    text = unit.replace(" ", "")
    if text in {"", "1"}:
        return DIMENSIONLESS
    exps: dict[str, int] = {}
    sign = 1
    for i, part in enumerate(re.split(r"([*/])", text)):
        if part in {"*", "/"}:
            sign = 1 if part == "*" else -1
            continue
        if part == "1" and i == 0:
            continue
        m = _UNIT_TOKEN.match(part)
        if not m:
            raise UnitError(f"cannot parse unit token {part!r} in {unit!r}")
        exps[m.group(1)] = exps.get(m.group(1), 0) + sign * int(m.group(2) or 1)
    return frozenset((k, v) for k, v in exps.items() if v != 0)


def _combine(a, b, sign: int) -> frozenset:
    out = dict(a)
    for k, v in b:
        out[k] = out.get(k, 0) + sign * v
    return frozenset((k, v) for k, v in out.items() if v != 0)


def formula_unit(node, units: dict[str, frozenset]) -> frozenset:
    if isinstance(node, FormulaRef):
        if node.variable_id not in units:
            raise UnitError(f"no unit for {node.variable_id}")
        return units[node.variable_id]
    if isinstance(node, FormulaConst):
        return DIMENSIONLESS
    left, right = formula_unit(node.left, units), formula_unit(node.right, units)
    if node.op in {"add", "sub"}:
        if left != right:
            raise UnitError(f"{node.op} of incompatible units {sorted(left)} vs {sorted(right)}")
        return left
    return _combine(left, right, 1 if node.op == "mul" else -1)


# ── formula admission checks ───────────────────────────────────────────────


@dataclass(frozen=True)
class FormulaCheck:
    ok: bool
    problems: tuple[str, ...]
    missing_parents: tuple[str, ...]
    derived_edges: tuple[tuple[str, str], ...] = field(default=())   # (parent_id, edge kind)


def derive_structural_edges(d: MetricDefinition) -> tuple[tuple[str, str], ...]:
    """A simple ratio num/den yields AGGREGATION_COMPOSITION edges; any other formula EXACT_ARITHMETIC."""
    f = d.formula_ast
    if (d.aggregation_kind == "ratio" and isinstance(f, FormulaBinOp) and f.op == "div"
            and isinstance(f.left, FormulaRef) and isinstance(f.right, FormulaRef)):
        return ((f.left.variable_id, "AGGREGATION_COMPOSITION"), (f.right.variable_id, "AGGREGATION_COMPOSITION"))
    return tuple((ref, "EXACT_ARITHMETIC") for ref in sorted(formula_refs(f)))


def check_formula(d: MetricDefinition, parents: dict[str, object]) -> FormulaCheck:
    """Units, population and grain must line up exactly for an *exact* identity.

    `parents` maps referenced ids to their current definitions (any kind; non-metrics fail).
    Missing conditional fields are reported as problems, never defaulted (comparison §5).
    """
    if d.formula_ast is None:
        return FormulaCheck(ok=True, problems=(), missing_parents=())
    refs = sorted(formula_refs(d.formula_ast))
    missing = tuple(r for r in refs if r not in parents)
    if missing:
        return FormulaCheck(ok=False, problems=(f"PARENT_NOT_ADMITTED: {list(missing)}",), missing_parents=missing)
    problems: list[str] = []
    for r in refs:
        if not isinstance(parents[r], MetricDefinition):
            problems.append(f"NOT_A_METRIC: {r} is a {parents[r].kind} variable")
    if problems:
        return FormulaCheck(ok=False, problems=tuple(problems), missing_parents=())
    try:
        units = {r: parse_unit(parents[r].unit) for r in refs}
        computed = formula_unit(d.formula_ast, units)
        declared = parse_unit(d.unit)
        if computed != declared:
            problems.append(f"UNIT_MISMATCH: formula yields {sorted(computed) or 'dimensionless'}, "
                            f"declared {sorted(declared) or 'dimensionless'}")
    except UnitError as exc:
        problems.append(f"UNIT_ERROR: {exc}")
    pops = {r: parents[r].population_coverage for r in refs}
    if d.population_coverage is None or any(p is None for p in pops.values()):
        problems.append("MISSING_POPULATION: exact identity needs population on the metric and every parent")
    else:
        mismatched = [r for r, p in pops.items() if p.population_id != d.population_coverage.population_id]
        if mismatched:
            problems.append(f"POPULATION_MISMATCH: {mismatched} not over {d.population_coverage.population_id}")
    grains = {r: parents[r].time_semantics for r in refs}
    if d.time_semantics is None or any(g is None for g in grains.values()):
        problems.append("MISSING_TIME_SEMANTICS: exact identity needs a grain on the metric and every parent")
    else:
        off = [r for r, g in grains.items() if g.grain != d.time_semantics.grain]
        if off:
            problems.append(f"GRAIN_MISMATCH: {off} not at grain {d.time_semantics.grain}")
    return FormulaCheck(ok=not problems, problems=tuple(problems), missing_parents=(),
                        derived_edges=derive_structural_edges(d) if not problems else ())


# ── numerical identity (v2.1 tol 1e-4 preserved; v3.2 §3.3 step 6) ─────────


def evaluate(node, values: dict[str, float]) -> float | None:
    """Evaluate the AST. None if an input is missing or a denominator is zero (undefined, never 0)."""
    if isinstance(node, FormulaRef):
        v = values.get(node.variable_id)
        return None if v is None else float(v)
    if isinstance(node, FormulaConst):
        return node.value
    a, b = evaluate(node.left, values), evaluate(node.right, values)
    if a is None or b is None:
        return None
    if node.op == "add":
        return a + b
    if node.op == "sub":
        return a - b
    if node.op == "mul":
        return a * b
    return None if b == 0 else a / b


@dataclass(frozen=True)
class IdentityResult:
    verdict: str                       # VERIFIED_IDENTITY | IDENTITY_MISMATCH | UNVERIFIED
    max_abs_residual: float | None
    windows_checked: int
    excluded: tuple[tuple[str, str], ...]   # (window, reason)


def verify_identity(formula, derived_id: str, rows: list[tuple[str, dict[str, float | None]]],
                    tolerance: float, min_windows: int) -> IdentityResult:
    """rows: (window_key, {variable_id: value}) aligned per window."""
    residuals, excluded = [], []
    for key, vals in rows:
        observed = vals.get(derived_id)
        implied = evaluate(formula, vals)
        if implied is None:
            excluded.append((key, "ZERO_DENOMINATOR_OR_PARENT_MISSING"))
        elif observed is None:
            excluded.append((key, "DERIVED_VALUE_MISSING"))
        elif not math.isfinite(implied):
            excluded.append((key, "NON_FINITE"))
        else:
            residuals.append(abs(observed - implied))
    if len(residuals) < min_windows:
        return IdentityResult("UNVERIFIED", max(residuals) if residuals else None, len(residuals), tuple(excluded))
    worst = max(residuals)
    return IdentityResult("VERIFIED_IDENTITY" if worst <= tolerance else "IDENTITY_MISMATCH", worst,
                          len(residuals), tuple(excluded))
