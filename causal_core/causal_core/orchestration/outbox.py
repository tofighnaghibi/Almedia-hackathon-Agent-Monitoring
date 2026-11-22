"""Transactional outbox dispatcher (v3.2 §5.1 crash window, I14; plan §E.5; CP-014).

A revision is persisted together with its outbox row before anything is published. Draining marks
a row SENT (attempts += 1), publishes, then marks it ACKED. After a crash, every non-ACKED row is
re-published with the *same* idempotency key:

- the sink honours idempotency keys → exactly-once *visible* output;
- the sink does not → at-least-once. Duplicates are possible, and this is reported, never hidden.
"""
from __future__ import annotations

from ..contracts.tasks import TaskRevision
from ..storage.ledger import Ledger


class OutboxDispatcher:
    def __init__(self, ledger: Ledger, sink):
        self.ledger, self.sink = ledger, sink

    @property
    def delivery_guarantee(self) -> str:
        return "EXACTLY_ONCE_VISIBLE" if getattr(self.sink, "supports_idempotency", False) else "AT_LEAST_ONCE"

    def drain(self) -> list[tuple[int, str]]:
        out = []
        for outbox_id, _task, _rev, _key, payload, _status, _attempts in self.ledger.unacked_outbox():
            self.ledger.mark_outbox(outbox_id, "SENT", bump=True)
            self.ledger.fault("outbox:after_mark_sent_before_publish")
            receipt = self.sink.publish(TaskRevision.model_validate_json(payload))
            self.ledger.fault("outbox:after_publish_before_ack")
            self.ledger.mark_outbox(outbox_id, "ACKED")
            out.append((outbox_id, receipt))
        return out
