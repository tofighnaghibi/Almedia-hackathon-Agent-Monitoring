"""The metric graph: a causal DAG over business metrics, across abstraction levels.

L1  Global and country totals (breakdown edges: sums over platform and category)
L2  Segments: country x platform x advertiser category, with causal mechanisms
L3  Users: individual accounts, scored by the taste model (only on demand)
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

MECHANISMS = ["new_accounts", "active_users", "impressions", "starts", "completions", "revenue", "rewards", "quality"]


@dataclass
class Node:
    id: str
    label: str
    unit: str
    kind: str              # count | money | ratio
    level: str             # L1/L2 (L1 nodes are breakdown sums of L2 nodes)
    parents: list[str] = field(default_factory=list)
    edge: str = "mechanism"  # root | mechanism | formula
    mechanism: str = ""
    expr: str | None = None
    aggregation: str = "sum"  # sum | ratio
    origin: str = "core"      # core | extension
    confidence: float = 1.0
    description: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("id", "label", "unit", "kind", "level", "parents", "edge", "mechanism",
                                               "expr", "aggregation", "origin", "confidence", "description", "note")}


CORE_NODES: list[Node] = [
    Node("new_accounts", "New accounts", "accounts/h", "count", "L2", [], "root",
         "Sign-ups per hour (seasonal baseline)", description="Accounts created per hour."),
    Node("active_users", "Active users", "users/h", "count", "L2", ["new_accounts"], "mechanism",
         "Returning users follow the daily rhythm; new accounts stay ~3 h on day one",
         description="Users active on the offer wall."),
    Node("impressions", "Offer impressions", "views/h", "count", "L2", ["active_users"], "mechanism",
         "Feed allocation: impressions per active user per category", description="Offers shown on the wall."),
    Node("starts", "Offer starts", "starts/h", "count", "L2", ["impressions"], "mechanism",
         "Start rate: how attractive the offer is to these users", description="Users who start an offer."),
    Node("completions", "Completions", "completions/h", "count", "L2", ["starts"], "mechanism",
         "Completion rate", description="Offers finished (e.g. reached level 10, signed up)."),
    Node("revenue", "Advertiser revenue", "USD/h", "money", "L2", ["completions"], "mechanism",
         "Payout per completion x share the advertiser confirms (postback / acceptance)",
         description="What advertisers pay Almedia."),
    Node("rewards", "User rewards", "USD/h", "money", "L2", ["completions"], "mechanism",
         "Reward per completion (share of payout, promos)", description="What Freecash pays users."),
    Node("quality", "Valuable users delivered", "users/h", "count", "L2", ["completions"], "mechanism",
         "Share of completions that become retained, paying players for the advertiser",
         description="Advertiser-side value (retention / payer index)."),
    Node("margin", "Contribution margin", "USD/h", "money", "L2", ["revenue", "rewards"], "formula",
         expr="revenue - rewards", description="Revenue minus rewards."),
    Node("quality_per_completion", "Quality per completion", "ratio", "ratio", "L2", ["quality", "completions"],
         "formula", expr="quality / completions", aggregation="ratio",
         description="How valuable an average completion is for the advertiser."),
]

KPI_WATCH = ["new_accounts", "completions", "revenue", "rewards", "margin", "quality_per_completion"]


class MetricGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {n.id: n for n in CORE_NODES}

    def add(self, node: Node) -> None:
        self.nodes[node.id] = node

    def upstream(self, node_id: str) -> list[str]:
        """All ancestors in topological order (roots first)."""
        seen: list[str] = []

        def visit(n: str) -> None:
            for p in self.nodes[n].parents:
                if p not in seen:
                    visit(p)
            if n not in seen:
                seen.append(n)
        visit(node_id)
        return seen

    def breakdown_dims(self, node_id: str) -> list[str]:
        """Dimensions a metric can be broken down by (users have no offer category; formulas inherit from inputs)."""
        n = self.nodes[node_id]
        if n.edge == "formula" and n.parents:
            dims = None
            for p in n.parents:
                d = self.breakdown_dims(p)
                dims = d if dims is None else [x for x in dims if x in d]
            return dims or []
        return ["country", "platform"] if node_id in ("new_accounts", "active_users") else \
            ["country", "platform", "offer category"]

    def to_dict(self) -> dict:
        nodes = [{**n.to_dict(), "breakdown": self.breakdown_dims(n.id)} for n in self.nodes.values()]
        edges = [{"source": p, "target": n.id, "type": n.edge if n.edge == "formula" else "mechanism",
                  "label": EDGE_TYPES["formula" if n.edge == "formula" else "mechanism"]["label"]}
                 for n in self.nodes.values() for p in n.parents]
        edges += [{"source": "calendar", "target": t, "type": "exogenous", "label": EDGE_TYPES["exogenous"]["label"]}
                  for t in ("active_users", "starts", "rewards")]
        # Breakdown edges (separate list, targets are dimension nodes): metric -> first dimension, then the chain
        # country -> platform -> offer category.
        breakdown_edges = [{"source": n.id, "target": "dim:" + self.breakdown_dims(n.id)[0].replace(" ", "_"),
                   "type": "breakdown", "label": EDGE_TYPES["breakdown"]["label"]}
                  for n in self.nodes.values() if self.breakdown_dims(n.id)]
        breakdown_edges += [{"source": f"dim:{a}", "target": f"dim:{b}", "type": "breakdown",
                   "label": EDGE_TYPES["breakdown"]["label"]}
                  for a, b in (("country", "platform"), ("platform", "offer_category"))]
        return {"nodes": nodes, "edges": edges, "breakdown_edges": breakdown_edges,
                "dimensions": [{"id": "dim:country", "label": "Country (20)"},
                               {"id": "dim:platform", "label": "Platform (iOS, Android)"},
                               {"id": "dim:offer_category", "label": "Offer category (6)"}],
                "exogenous": [{"id": "calendar", "label": "Planned events (marketing calendar)"}],
                "edge_types": [{"type": k, **v} for k, v in EDGE_TYPES.items()],
                "levels": {"L1": "Global and country totals", "L2": "Country x platform x advertiser category",
                           "L3": "Individual accounts (taste model, on demand)"}}

    def tree(self) -> dict:
        """Browsable tree: business outcomes at the top, then formula inputs, then possible causes, with breakdowns."""
        used = {p for n in self.nodes.values() for p in n.parents}
        outcomes = [r for r in ("margin", "quality_per_completion") if r in self.nodes]
        roots = outcomes + [n.id for n in self.nodes.values() if n.origin == "extension"] + \
            [n.id for n in self.nodes.values() if n.id not in used and n.id not in outcomes and n.origin != "extension"]
        rows: list[dict] = []
        shown: set[str] = set()

        def visit(nid: str, depth: int, relation: str | None) -> None:
            n = self.nodes[nid]
            again = nid in shown
            rows.append({"depth": depth, "id": nid, "label": n.label, "relation": relation,
                         "relation_label": EDGE_TYPES[relation]["label"] if relation else None,
                         "expr": n.expr, "unit": n.unit, "origin": n.origin,
                         "breakdown": " → ".join(["global"] + self.breakdown_dims(nid)), "repeat": again})
            if again:
                return
            shown.add(nid)
            rel = "formula" if n.edge == "formula" else "mechanism"
            for p in n.parents:
                visit(p, depth + 1, rel)

        for r in roots:
            visit(r, 0, None)
        return {"roots": roots, "rows": rows, "edge_types": [{"type": k, **v} for k, v in EDGE_TYPES.items()]}


EDGE_TYPES = {
    "formula": {"label": "Formula", "meaning": "the metric is computed from its inputs (e.g. margin = revenue − rewards)",
                "reasoning": "exact decomposition: how much each input contributes to a change"},
    "breakdown": {"label": "Breakdown", "meaning": "the same metric split by country → platform → offer category",
                  "reasoning": "localise: find the segment that carries the change"},
    "mechanism": {"label": "Possible cause", "meaning": "the upstream metric drives this one (e.g. starts → completions)",
                  "reasoning": "root cause: test each link given its parents, counterfactual proof"},
    "exogenous": {"label": "Known event", "meaning": "planned marketing events change these metrics on purpose",
                  "reasoning": "explain innocent changes instead of alerting"},
}


SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def parse_expression(expr: str, known: set[str]) -> tuple[list[str], str]:
    """Validate a metric formula: only + - * / ( ), numbers and known metric names."""
    tree = ast.parse(expr, mode="eval")
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id not in known:
                raise ValueError(f"unknown metric '{node.id}'")
            if node.id not in names:
                names.append(node.id)
        elif isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Load,
                               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub)):
            continue
        else:
            raise ValueError(f"unsupported syntax: {type(node).__name__}")
    has_div = any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div) for n in ast.walk(tree))
    return names, ("ratio" if has_div else "sum")


def evaluate(expr: str, values: dict) -> object:
    code = compile(ast.parse(expr, mode="eval"), "<metric>", "eval")
    return eval(code, {"__builtins__": {}}, values)  # noqa: S307 - names/ops validated by parse_expression
