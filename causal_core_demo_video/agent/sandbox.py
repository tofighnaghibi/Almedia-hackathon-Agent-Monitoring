"""Demo-only action layer: proposals, human approvals, sandbox execution, receipts.

Nothing here can reach production. The only executors are in-memory sandboxes owned by this folder: a mock Statsig
project and the marketplace simulator. The core's policy gate still DENIES every requested action (I15); this layer
is the human-approved path the video shows, and it refuses to run anything that is not approved, bound to this
exact action and scope, unexpired, and aimed at a sandbox target.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta


def _h(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


@dataclass(frozen=True)
class ActionProposal:
    action_id: str
    use_case: str
    sandbox: str                      # which sandbox may execute it
    target: str                       # e.g. experiment id or release id
    kind: str                         # annotate_freeze | set_rollout | rollback_release | adjust_promise | ...
    params: dict
    rationale: str
    expected: dict | None             # e.g. {"metric": ..., "mean": ..., "lower": ..., "upper": ...}
    risk: str                         # low | medium | high
    evidence_refs: tuple[str, ...]
    created_at: str

    @staticmethod
    def make(use_case, sandbox, target, kind, params, rationale, expected, risk, evidence_refs, created_at):
        aid = "act:" + _h(use_case, sandbox, target, kind, params, created_at)[:12]
        return ActionProposal(aid, use_case, sandbox, target, kind, params, rationale, expected, risk,
                              tuple(evidence_refs), created_at)


@dataclass(frozen=True)
class Approval:
    action_id: str
    approver: str
    approved_at: str
    expires_at: str
    token: str                        # bound to action id + sandbox + target + expiry

    @staticmethod
    def grant(p: ActionProposal, approver: str, at: datetime, valid_for=timedelta(hours=4)) -> "Approval":
        exp = at + valid_for
        return Approval(p.action_id, approver, at.isoformat(), exp.isoformat(),
                        _h(p.action_id, p.sandbox, p.target, exp.isoformat()))


@dataclass(frozen=True)
class Receipt:
    action_id: str
    sandbox: str
    executed_at: str
    idempotency_key: str
    result: str                       # EXECUTED | REFUSED
    reason: str
    state_after: dict = field(default_factory=dict)


class Sandbox:
    """Base: validates approval, executes once per action (idempotent), records receipts."""
    name = "sandbox"

    def __init__(self):
        self.receipts: dict[str, Receipt] = {}
        self.log: list[dict] = []

    def _refuse(self, p, now, why) -> Receipt:
        r = Receipt(p.action_id, self.name, now.isoformat(), _h("refused", p.action_id, why), "REFUSED", why)
        self.log.append(asdict(r))
        return r

    def execute(self, p: ActionProposal, a: Approval | None, now: datetime) -> Receipt:
        if p.action_id in self.receipts:
            return self.receipts[p.action_id]                       # retries never execute twice
        if p.sandbox != self.name:
            return self._refuse(p, now, f"target sandbox {p.sandbox!r} is not this sandbox ({self.name})")
        if a is None:
            return self._refuse(p, now, "no human approval")
        if a.action_id != p.action_id or a.token != _h(p.action_id, p.sandbox, p.target, a.expires_at):
            return self._refuse(p, now, "approval token is not bound to this action, sandbox and target")
        if now > datetime.fromisoformat(a.expires_at):
            return self._refuse(p, now, "approval expired")
        state = self.apply(p, now)
        r = Receipt(p.action_id, self.name, now.isoformat(), _h("exec", p.action_id), "EXECUTED", "approved; executed in sandbox",
                    state)
        self.receipts[p.action_id] = r
        self.log.append(asdict(r))
        return r

    def apply(self, p: ActionProposal, now: datetime) -> dict:      # pragma: no cover - overridden
        raise NotImplementedError


class MockStatsigProject(Sandbox):
    """An in-memory stand-in for a Statsig project: experiment status, rollout split, annotations, tickets."""
    name = "mock-statsig-project"

    def __init__(self, experiments: dict[str, dict]):
        super().__init__()
        self.state = copy.deepcopy(experiments)    # id -> {"status", "rollout": {group: pct}, "annotations": [], "decision_frozen"}
        self.tickets: list[dict] = []

    def apply(self, p, now):
        e = self.state[p.target]
        if p.kind == "annotate_freeze":
            e["annotations"].append({"at": now.isoformat(), "text": p.params["note"]})
            e["decision_frozen"] = True
            self.tickets.append({"at": now.isoformat(), "title": p.params["ticket"], "experiment": p.target})
        elif p.kind == "set_rollout":
            e["rollout"] = dict(p.params["rollout"])
            e["status"] = p.params.get("status", e["status"])
            e["annotations"].append({"at": now.isoformat(), "text": p.params.get("note", "rollout changed")})
        else:
            raise ValueError(f"unsupported action kind {p.kind}")
        return {"experiment": p.target, **copy.deepcopy(e)}


class LocalShopFlags(Sandbox):
    """The local Astronomy Shop's feature-flag file (read by flagd). This executor can change only the flags it is
    given, only to variants that exist, and only after a valid approval; every change is read back through flagd."""
    name = "local-astronomy-shop"

    def __init__(self, flag_adapter, allowed: tuple[str, ...] = ("paymentFailure",)):
        super().__init__()
        self.flags, self.allowed = flag_adapter, tuple(allowed)

    def apply(self, p: ActionProposal, now: datetime) -> dict:
        if p.kind != "set_flag" or p.params.get("flag") not in self.allowed:
            raise ValueError(f"{p.kind} on {p.params.get('flag')!r} is not allowed in {self.name}")
        flag, variant = p.params["flag"], p.params["variant"]
        before = self.flags.read(flag)
        ok = self.flags.set_variant(flag, variant)
        return {"flag": flag, "before": before, "after": self.flags.read(flag), "read_back_ok": ok}
