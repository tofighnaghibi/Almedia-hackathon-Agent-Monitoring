"""Durable graph-delta log on SQLite (v3.2 §3.2, §5.1; plan §D.6; CP-009).

One admission = one `BEGIN IMMEDIATE` transaction: the version row, every delta row and the source
record key commit together or not at all. The in-memory GraphStore is updated only after COMMIT,
and can always be rebuilt by replaying this log. Phase 3 folds this into the full single-writer
ledger. The table shapes here are its graph slice.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterator

from pydantic import TypeAdapter

from ..contracts.evidence import EffectClaim
from ..contracts.graph import GraphEdge, GraphNode, StructuralCertificate, Validity
from ..contracts.variables import VariableDefinition
from ..graph.store import ClaimRecord, GraphDelta, GraphStore, NodeRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_versions (
    version             INTEGER PRIMARY KEY,
    request_id          TEXT NOT NULL UNIQUE,
    request_fingerprint TEXT NOT NULL,
    committed_at        TEXT NOT NULL,
    verdict_json        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS graph_deltas (
    version    INTEGER NOT NULL REFERENCES graph_versions(version),
    seq        INTEGER NOT NULL,
    entity     TEXT NOT NULL CHECK (entity IN ('node', 'edge', 'certificate', 'claim')),
    entity_id  TEXT NOT NULL,
    payload    TEXT NOT NULL,
    PRIMARY KEY (version, seq)
);
CREATE INDEX IF NOT EXISTS graph_deltas_entity ON graph_deltas(entity, entity_id, version);
CREATE TABLE IF NOT EXISTS source_records (
    source_id    TEXT NOT NULL,
    record_id    TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    version      INTEGER NOT NULL REFERENCES graph_versions(version),
    PRIMARY KEY (source_id, record_id)
);
"""

_DEF = TypeAdapter(VariableDefinition)
_NODE, _EDGE, _VAL = TypeAdapter(GraphNode), TypeAdapter(GraphEdge), TypeAdapter(Validity)
_CERT, _CLAIM = TypeAdapter(StructuralCertificate), TypeAdapter(EffectClaim)


class InjectedCrash(RuntimeError):
    """Raised by the fault-injection hook to simulate a crash mid-transaction."""


def connect(path: str | Path) -> sqlite3.Connection:
    """One connection per process: WAL, foreign keys, busy timeout; autocommit so we BEGIN explicitly."""
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=5.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


class GraphLog:
    """Graph slice of the ledger. Standalone (own connection) or sharing a Ledger's connection + writer lock."""

    def __init__(self, path_or_conn: str | Path | sqlite3.Connection, *, writer_lock=None,
                 fail_after_rows: int | None = None):
        if isinstance(path_or_conn, sqlite3.Connection):
            self._conn, self.path = path_or_conn, None
        else:
            self._conn, self.path = connect(path_or_conn), str(path_or_conn)
        self._conn.executescript(SCHEMA)
        self._lock = writer_lock or threading.RLock()
        self.fail_after_rows = fail_after_rows
        self.fault = None          # set by Ledger: named fault points

    def close(self) -> None:
        self._conn.close()

    def head(self) -> int:
        return self._conn.execute("SELECT COALESCE(MAX(version), 0) FROM graph_versions").fetchone()[0]

    def source_record(self, source_id: str, record_id: str) -> tuple[str, int] | None:
        return self._conn.execute("SELECT content_hash, version FROM source_records WHERE source_id=? AND record_id=?",
                                  (source_id, record_id)).fetchone()

    def commit(self, delta: GraphDelta) -> None:
        rows = []
        for r in delta.nodes:
            rows.append(("node", r.node.node_id, json.dumps({
                "node": json.loads(r.node.model_dump_json()),
                "definition": json.loads(r.definition.model_dump_json()) if r.definition is not None else None,
                "validity": json.loads(r.validity.model_dump_json())})))
        rows += [("edge", e.edge_id, e.model_dump_json()) for e in delta.edges]
        rows += [("certificate", c.certificate_id, c.model_dump_json()) for c in delta.certificates]
        rows += [("claim", c.claim.claim_id, json.dumps({"treatment_node": c.treatment_node,
                                                         "outcome_node": c.outcome_node,
                                                         "claim": json.loads(c.claim.model_dump_json())}))
                 for c in delta.claims]
        cur = self._conn
        with self._lock:
            self._commit_locked(cur, delta, rows)

    def _commit_locked(self, cur, delta: GraphDelta, rows) -> None:
        cur.execute("BEGIN IMMEDIATE")
        try:
            if delta.version != self.head() + 1:
                raise ValueError(f"delta version {delta.version} does not follow log head {self.head()}")
            cur.execute("INSERT INTO graph_versions VALUES (?,?,?,?,?)",
                        (delta.version, delta.request_id, delta.request_fingerprint,
                         delta.committed_at.isoformat(), delta.verdict_json))
            for seq, (entity, eid, payload) in enumerate(rows):
                if self.fail_after_rows is not None and seq >= self.fail_after_rows:
                    raise InjectedCrash(f"injected crash after {seq} delta rows")
                cur.execute("INSERT INTO graph_deltas VALUES (?,?,?,?,?)", (delta.version, seq, entity, eid, payload))
                if seq == 0 and self.fault is not None:
                    self.fault("graph:mid_commit")      # inside the open transaction
            cur.execute("INSERT INTO source_records VALUES (?,?,?,?)",
                        (delta.source_id, delta.record_id, delta.request_fingerprint, delta.version))
            cur.execute("COMMIT")
        except BaseException:
            cur.execute("ROLLBACK")
            raise

    def replay(self) -> Iterator[GraphDelta]:
        versions = self._conn.execute(
            "SELECT version, request_id, request_fingerprint, committed_at, verdict_json FROM graph_versions "
            "ORDER BY version").fetchall()
        for version, request_id, fp, committed_at, verdict in versions:
            src = self._conn.execute("SELECT source_id, record_id FROM source_records WHERE version=?",
                                     (version,)).fetchone()
            nodes, edges, certs, claims = [], [], [], []
            for entity, payload in self._conn.execute(
                    "SELECT entity, payload FROM graph_deltas WHERE version=? ORDER BY seq", (version,)):
                if entity == "node":
                    p = json.loads(payload)
                    nodes.append(NodeRecord(_NODE.validate_json(json.dumps(p["node"])),
                                            _DEF.validate_json(json.dumps(p["definition"])) if p["definition"] else None,
                                            _VAL.validate_json(json.dumps(p["validity"]))))
                elif entity == "edge":
                    edges.append(_EDGE.validate_json(payload))
                elif entity == "certificate":
                    certs.append(_CERT.validate_json(payload))
                else:
                    p = json.loads(payload)
                    claims.append(ClaimRecord(p["treatment_node"], p["outcome_node"],
                                              _CLAIM.validate_json(json.dumps(p["claim"]))))
            yield GraphDelta(version, request_id, fp, src[0], src[1], datetime.fromisoformat(committed_at),
                             tuple(nodes), tuple(edges), tuple(certs), tuple(claims), verdict)


def rebuild(log: GraphLog) -> GraphStore:
    """Crash recovery: the in-memory index is always derivable from committed deltas alone."""
    store = GraphStore()
    for delta in log.replay():
        store.apply(delta)
    return store
