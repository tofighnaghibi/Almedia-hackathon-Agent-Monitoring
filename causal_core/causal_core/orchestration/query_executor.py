"""Budgeted, retry-safe evidence queries (v3.2 §5.2, §5.4; plan §E.4; CP-013).

1. A recorded RESPONDED fingerprint is replayed: no charge, no dispatch. Replay uses the recorder.
2. Otherwise the charge is persisted with status ISSUED *before* dispatch, decrementing the job's
   remaining query budget. An insufficient budget raises BudgetExhausted before anything is sent.
3. A crash between dispatch and persistence leaves ISSUED. The retry re-dispatches under the same
   fingerprint *without charging again*: execution is at-least-once, the charge exactly once.
"""
from __future__ import annotations

import hashlib
import json

from ..contracts.capabilities import CapabilityResponse
from ..storage.ledger import Ledger


class BudgetExhausted(RuntimeError):
    pass


def cache_key(*, scope_id: str, data_cut: str, request_fingerprint: str, graph_version: int,
              definition_versions: dict[str, int], method_version: str = "-", seed: int | None = None) -> str:
    """§5.4: scope + data cut + content + graph pin + definition versions + method + seed. Never id alone."""
    blob = json.dumps({"scope": scope_id, "cut": data_cut, "req": request_fingerprint, "graph": graph_version,
                       "defs": definition_versions, "method": method_version, "seed": seed}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


class BudgetedQueryExecutor:
    def __init__(self, ledger: Ledger, port):
        self.ledger, self.port = ledger, port

    @staticmethod
    def fingerprint(request) -> str:
        return hashlib.sha256(request.model_dump_json().encode()).hexdigest()

    def execute(self, job_id: str, request, *, cost_units: int = 1, cache: str | None = None) -> CapabilityResponse:
        fp = self.fingerprint(request)
        rec = self.ledger.read("SELECT status, response_json FROM query_ledger WHERE request_fingerprint=?", (fp,))
        if rec and rec[0][0] == "RESPONDED":
            return CapabilityResponse.model_validate_json(rec[0][1])
        if cache:
            hit = self.ledger.read("SELECT response_json FROM query_cache WHERE cache_key=?", (cache,))
            if hit:
                return CapabilityResponse.model_validate_json(hit[0][0])
        if not rec:
            with self.ledger.write() as cur:
                remaining = cur.execute("SELECT remaining_query_budget FROM jobs WHERE job_id=?", (job_id,)).fetchone()[0]
                if remaining < cost_units:
                    raise BudgetExhausted(f"job {job_id}: {remaining} query unit(s) left, {cost_units} needed")
                cur.execute("UPDATE jobs SET remaining_query_budget = remaining_query_budget - ? WHERE job_id=?",
                            (cost_units, job_id))
                cur.execute("INSERT INTO query_ledger VALUES (?,?,?, 'ISSUED', 0, ?, NULL)",
                            (fp, job_id, request.model_dump_json(), cost_units))
            self.ledger.fault("query:after_charge_before_dispatch")
        with self.ledger.write() as cur:
            cur.execute("UPDATE query_ledger SET dispatch_count = dispatch_count + 1 WHERE request_fingerprint=?", (fp,))
        response = self.port.query(request)
        self.ledger.fault("query:after_dispatch_before_persist")
        with self.ledger.write() as cur:
            cur.execute("UPDATE query_ledger SET status='RESPONDED', response_json=? WHERE request_fingerprint=?",
                        (response.model_dump_json(), fp))
            if cache:
                cur.execute("INSERT OR REPLACE INTO query_cache VALUES (?,?)", (cache, response.model_dump_json()))
        return response
