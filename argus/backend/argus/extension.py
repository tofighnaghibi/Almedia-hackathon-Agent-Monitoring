"""Automatic extension of the metric graph: resolve a user formula, check units, place it in the graph.

Pure functions (no world state); `Agent.add_metric` runs the data checks and starts monitoring.
"""
from __future__ import annotations

import ast
import difflib
import re
from collections import Counter

# Base dimension of each known metric (all counts are per hour, so "per hour" cancels in ratios).
DIMENSION = {
    "new_accounts": "count", "active_users": "count", "impressions": "count", "starts": "count",
    "completions": "count", "quality": "count", "revenue": "money", "rewards": "money", "margin": "money",
    "quality_per_completion": "ratio",
}
SINGULAR = {"new_accounts": "new account", "active_users": "active user", "impressions": "impression",
            "starts": "offer start", "completions": "completion", "quality": "valuable user"}
# Breakdown dimensions available for each metric (users have no offer category).
BREAKDOWN = {m: ["country", "platform", "offer category"] for m in DIMENSION}
BREAKDOWN["new_accounts"] = BREAKDOWN["active_users"] = ["country", "platform"]

# Words that map to exactly one metric (longest phrases first when applied).
ALIASES = {
    "new accounts": "new_accounts", "new account": "new_accounts", "new users": "new_accounts", "new user": "new_accounts",
    "signups": "new_accounts", "signup": "new_accounts", "sign ups": "new_accounts", "sign up": "new_accounts",
    "sign-ups": "new_accounts", "registrations": "new_accounts", "active users": "active_users",
    "active user": "active_users", "dau": "active_users", "impressions": "impressions", "impression": "impressions",
    "views": "impressions", "offer starts": "starts", "starts": "starts", "start": "starts",
    "completed offers": "completions", "completed offer": "completions", "completions": "completions",
    "completion": "completions", "advertiser revenue": "revenue", "revenue": "revenue", "gmv": "revenue",
    "user rewards": "rewards", "reward cost": "rewards", "rewards": "rewards", "reward": "rewards", "payouts": "rewards",
    "contribution margin": "margin", "margin": "margin", "profit": "margin", "valuable users": "quality",
    "quality": "quality", "quality per completion": "quality_per_completion",
}
# Words that could mean more than one metric: the agent asks instead of guessing.
AMBIGUOUS = {
    "users": ["active_users", "new_accounts"], "user": ["active_users", "new_accounts"],
    "accounts": ["new_accounts", "active_users"], "account": ["new_accounts", "active_users"],
    "conversions": ["completions", "starts"], "conversion": ["completions", "starts"],
    "value": ["revenue", "quality"], "cost": ["rewards", "revenue"], "costs": ["rewards", "revenue"],
}
OPS = [(" divided by ", " / "), (" per ", " / "), (" minus ", " - "), (" plus ", " + "), (" times ", " * ")]


def split_name_formula(name: str, description: str = "", formula: str | None = None) -> tuple[str, str | None]:
    """'margin_per_signup = margin / signups' -> ('margin_per_signup', 'margin / signups')."""
    name = (name or "").strip()
    if formula:
        return name, formula.strip()
    if "=" in name:
        left, right = name.split("=", 1)
        return left.strip(), right.strip()
    if "=" in (description or ""):
        left, right = description.split("=", 1)
        return (name or left.strip()), right.strip()
    return name, None


def resolve_formula(text: str, known: set[str]) -> dict:
    """Map a formula written with metric ids or business words onto known metrics.

    Returns {"expr", "mapped": [(word, id)], "ambiguous": {word: [ids]}, "unknown": {word: [suggestions]}}.
    """
    s = " " + text.lower().strip() + " "
    for a, b in OPS:
        s = s.replace(a, b)
    mapped: list[tuple[str, str]] = []
    for phrase in sorted(ALIASES, key=len, reverse=True):
        pat = rf"(?<![a-z_]){re.escape(phrase)}(?![a-z_])"
        if re.search(pat, s):
            target = ALIASES[phrase]
            if phrase != target:
                mapped.append((phrase, target))
            s = re.sub(pat, f" {target} ", s)
    tokens = re.findall(r"[a-z_][a-z0-9_]*|\d+\.?\d*|[()+\-*/]", s)
    ambiguous, unknown = {}, {}
    for tok in tokens:
        if not tok[0].isalpha() and tok[0] != "_":
            continue
        if tok in known:
            continue
        if tok in AMBIGUOUS:
            ambiguous[tok] = AMBIGUOUS[tok]
        else:
            close = difflib.get_close_matches(tok, sorted(known) + sorted(ALIASES), n=5, cutoff=0.6)
            ids = []
            for c in close:
                cid = c if c in known else ALIASES.get(c, c)
                if cid not in ids:
                    ids.append(cid)
            unknown[tok] = ids[:3]
    return {"expr": re.sub(r"\s+", " ", " ".join(tokens)).strip(), "mapped": mapped, "ambiguous": ambiguous,
            "unknown": unknown}


class DimensionError(ValueError):
    pass


def dimension(expr: str) -> Counter:
    """Units of a formula as a Counter of base units (USD, or the counted thing); raises on unit mismatch."""
    tree = ast.parse(expr, mode="eval")

    def unit_of(name: str) -> Counter:
        kind = DIMENSION.get(name, "ratio")
        return Counter({"USD": 1}) if kind == "money" else Counter({name: 1}) if kind == "count" else Counter()

    def walk(n) -> Counter | None:              # None = plain number (dimensionless constant)
        if isinstance(n, ast.Expression):
            return walk(n.body)
        if isinstance(n, ast.Constant):
            return None
        if isinstance(n, ast.Name):
            return unit_of(n.id)
        if isinstance(n, ast.UnaryOp):
            return walk(n.operand)
        if isinstance(n, ast.BinOp):
            a, b = walk(n.left), walk(n.right)
            if isinstance(n.op, (ast.Add, ast.Sub)):
                if a is None or b is None:
                    raise DimensionError("adding a plain number to a metric has no business meaning")
                if +a != +b:
                    raise DimensionError(f"cannot add or subtract {unit_text(a)} and {unit_text(b)}")
                return a
            a, b = a or Counter(), b or Counter()
            out = Counter(a)
            for k, v in b.items():
                out[k] += v if isinstance(n.op, ast.Mult) else -v
            return Counter({k: v for k, v in out.items() if v != 0})
        raise DimensionError(f"unsupported syntax: {type(n).__name__}")
    return walk(tree) or Counter()


def unit_text(u: Counter) -> str:
    def part(k: str) -> str:
        return "USD" if k == "USD" else SINGULAR.get(k, k)
    num = [part(k) for k, v in sorted(u.items()) for _ in range(v) if v > 0]
    den = [part(k) for k, v in sorted(u.items()) for _ in range(-v) if v < 0]
    if not num and not den:
        return "ratio"
    top = " × ".join(num) if num else "1"
    if not den:
        return top + ("/h" if top == "USD" else " per hour")
    return f"{top} per {' × '.join(den)}"


def placement(names: list[str]) -> dict:
    """Where a new formula metric belongs: under its inputs, broken down by the dimensions they share."""
    dims = [d for d in ["country", "platform", "offer category"] if all(d in BREAKDOWN.get(n, []) for n in names)]
    return {"parents": names, "edge_type": "formula", "level": "L2", "breakdown": ["global"] + dims}
