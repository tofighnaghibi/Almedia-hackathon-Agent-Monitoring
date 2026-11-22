"""Safe AST-based formula parser and evaluator for metric graph edges."""

from __future__ import annotations

import ast
from typing import Any

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div)
_ALLOWED_UNARYOPS = (ast.USub,)


def _validate_ast(node: ast.AST, names: set[str]) -> None:
    """Recursively validate AST nodes against a strict arithmetic whitelist."""
    if isinstance(node, ast.Expression):
        _validate_ast(node.body, names)
        return
    if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
        _validate_ast(node.left, names)
        _validate_ast(node.right, names)
        return
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, _ALLOWED_UNARYOPS):
        _validate_ast(node.operand, names)
        return
    if isinstance(node, ast.Name):
        names.add(node.id)
        return
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return
    raise ValueError(f"Disallowed syntax in formula: {ast.dump(node)}")


def parse_formula(formula: str) -> tuple[str, list[str], Any]:
    """Parse ``'lhs = rhs'`` into ``(lhs, sorted_input_names, compiled_code)``.

    Only names, numeric constants, ``+ - * /``, and unary ``-`` are permitted.
    """
    if not isinstance(formula, str) or formula.count("=") != 1:
        raise ValueError(f"Formula must contain exactly one '=': {formula!r}")

    lhs_raw, rhs_raw = formula.split("=", 1)
    lhs = lhs_raw.strip()
    rhs = rhs_raw.strip()

    if not lhs or not lhs.isidentifier():
        raise ValueError(f"Invalid left-hand side metric name: {lhs!r}")
    if not rhs:
        raise ValueError("Formula right-hand side cannot be empty")

    try:
        tree = ast.parse(rhs, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid formula syntax: {formula!r}") from exc

    names: set[str] = set()
    _validate_ast(tree, names)
    compiled = compile(ast.fix_missing_locations(tree), filename="<formula>", mode="eval")
    return lhs, sorted(names), compiled


def eval_formula(compiled_code: Any, env: dict[str, Any]) -> Any:
    """Evaluate compiled formula code with no builtins."""
    return eval(compiled_code, {"__builtins__": {}}, env)  # noqa: S307
