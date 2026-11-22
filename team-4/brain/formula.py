"""Safe arithmetic formulas over metric names.

Only names, numbers, parentheses, unary minus and + - * / are allowed. The parsed formula
knows its shape (sum, product, ratio), which later drives contribution decomposition.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

_BINOPS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}


class FormulaError(ValueError):
    pass


def _check(node: ast.AST) -> None:
    if isinstance(node, ast.Expression):
        _check(node.body)
    elif isinstance(node, ast.BinOp):
        if type(node.op) not in _BINOPS:
            raise FormulaError(f"operator not allowed: {type(node.op).__name__}")
        _check(node.left)
        _check(node.right)
    elif isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, (ast.USub, ast.UAdd)):
            raise FormulaError(f"operator not allowed: {type(node.op).__name__}")
        _check(node.operand)
    elif isinstance(node, ast.Name):
        pass
    elif isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
            raise FormulaError(f"only numeric constants are allowed, got {node.value!r}")
    else:
        raise FormulaError(f"not allowed in a formula: {type(node).__name__}")


def _names(node: ast.AST) -> list[str]:
    out: list[str] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id not in out:
            out.append(n.id)
    # ast.walk is breadth-first; re-sort by source position for a stable reading order
    pos = {n.id: (n.lineno, n.col_offset) for n in ast.walk(node) if isinstance(n, ast.Name)}
    return sorted(out, key=lambda k: pos[k])


def _flatten(node: ast.AST, ops: tuple[type, ...]) -> list[tuple[int, ast.AST]]:
    """Flatten a chain of + and - (or of *) into (sign, operand) pairs."""
    if isinstance(node, ast.BinOp) and type(node.op) in ops:
        left = _flatten(node.left, ops)
        right = _flatten(node.right, ops)
        if isinstance(node.op, ast.Sub):
            right = [(-s, n) for s, n in right]
        return left + right
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and ast.Add in ops:
        return [(-s, n) for s, n in _flatten(node.operand, ops)]
    return [(1, node)]


@dataclass(frozen=True)
class Formula:
    text: str
    tree: ast.Expression = field(repr=False, compare=False)
    names: tuple[str, ...]

    # ------------------------------------------------------------------ shape
    @property
    def kind(self) -> str:
        """'sum' (names joined by + and -), 'product' (names joined by *),
        'ratio' (a / b of names or products), 'name' (a single name), or 'other'."""
        body = self.tree.body
        if isinstance(body, ast.Name):
            return "name"
        terms = _flatten(body, (ast.Add, ast.Sub))
        if len(terms) > 1 and all(isinstance(n, ast.Name) for _, n in terms):
            return "sum"
        factors = _flatten(body, (ast.Mult,))
        if len(factors) > 1 and all(isinstance(n, ast.Name) for _, n in factors):
            return "product"
        if isinstance(body, ast.BinOp) and isinstance(body.op, ast.Div):
            sides = [body.left, body.right]
            if all(all(isinstance(n, ast.Name) for _, n in _flatten(s, (ast.Mult,))) for s in sides):
                return "ratio"
        return "other"

    def terms(self) -> list[tuple[int, str]]:
        """(sign, name) for a sum."""
        if self.kind != "sum":
            raise FormulaError(f"{self.text!r} is not a sum")
        return [(s, n.id) for s, n in _flatten(self.tree.body, (ast.Add, ast.Sub))]

    def factors(self) -> list[str]:
        if self.kind != "product":
            raise FormulaError(f"{self.text!r} is not a product")
        return [n.id for _, n in _flatten(self.tree.body, (ast.Mult,))]

    def ratio_parts(self) -> tuple[list[str], list[str]]:
        """(numerator factors, denominator factors) for a ratio."""
        if self.kind != "ratio":
            raise FormulaError(f"{self.text!r} is not a ratio")
        body = self.tree.body
        num = [n.id for _, n in _flatten(body.left, (ast.Mult,))]
        den = [n.id for _, n in _flatten(body.right, (ast.Mult,))]
        return num, den

    def roles(self) -> dict[str, str]:
        """Role of each name: term+ / term- / factor / numerator / denominator / operand."""
        kind = self.kind
        if kind == "name":
            return {self.names[0]: "alias"}
        if kind == "sum":
            return {n: ("term+" if s > 0 else "term-") for s, n in self.terms()}
        if kind == "product":
            return {n: "factor" for n in self.factors()}
        if kind == "ratio":
            num, den = self.ratio_parts()
            roles = {n: "numerator" for n in num}
            roles.update({n: "denominator" for n in den})
            return roles
        return {n: "operand" for n in self.names}

    # ------------------------------------------------------------- evaluation
    def evaluate(self, env):
        """Evaluate with env[name] -> scalar, numpy array or pandas Series.
        Division by zero gives NaN, never inf."""
        return _eval(self.tree.body, env)

    def __str__(self) -> str:
        return self.text


def _div(a, b):
    if isinstance(a, pd.Series) or isinstance(b, pd.Series):
        out = a / b
        return out.replace([np.inf, -np.inf], np.nan) if isinstance(out, pd.Series) else out
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.divide(np.asarray(a, dtype=float), np.asarray(b, dtype=float))
    out = np.where(np.isfinite(out), out, np.nan)
    return float(out) if out.ndim == 0 else out


def _eval(node: ast.AST, env):
    if isinstance(node, ast.Name):
        try:
            return env[node.id]
        except KeyError:
            raise FormulaError(f"unknown name in formula: {node.id}") from None
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, env)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left, env), _eval(node.right, env)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        return _div(a, b)
    raise FormulaError(f"cannot evaluate {type(node).__name__}")


def parse(text: str) -> Formula:
    if not isinstance(text, str) or not text.strip():
        raise FormulaError("empty formula")
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as e:
        raise FormulaError(f"cannot parse formula {text!r}: {e.msg}") from None
    _check(tree)
    return Formula(text=text.strip(), tree=tree, names=tuple(_names(tree)))
