"""A provider stub that records every real dispatch in its own SQLite file (the outside world).

Used to prove that a crash never double-*charges* a query, while showing honestly that a query can
be *dispatched* again (at-least-once) when the crash falls between dispatch and persistence.
"""
from __future__ import annotations

import hashlib
import sqlite3

from causal_core.contracts.capabilities import CapabilityResponse


class RecordingPort:
    def __init__(self, path):
        self._conn = sqlite3.connect(str(path), isolation_level=None)
        self._conn.execute("CREATE TABLE IF NOT EXISTS dispatches (n INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT)")

    def query(self, request) -> CapabilityResponse:
        self._conn.execute("INSERT INTO dispatches (request_id) VALUES (?)", (request.request_id,))
        return CapabilityResponse(schema_version="2.0", request_id=request.request_id,
                                  request_fingerprint=hashlib.sha256(request.model_dump_json().encode()).hexdigest(),
                                  status="OK", completeness=1.0)

    def dispatch_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM dispatches").fetchone()[0]

    def close(self) -> None:
        self._conn.close()
