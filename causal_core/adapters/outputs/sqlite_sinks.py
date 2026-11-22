"""Output sinks backed by their own SQLite file: the "outside world" that survives a crash of the core.

IdempotentSink honours the revision's idempotency key (INSERT OR IGNORE). AtLeastOnceSink does not.
Both record every *visible* publication so a test can count duplicates after a kill -9.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from causal_core.contracts.tasks import TaskRevision


class _Sink:
    supports_idempotency = False

    def __init__(self, path: str | Path):
        self._conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)   # one connection, autocommit
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("CREATE TABLE IF NOT EXISTS visible (n INTEGER PRIMARY KEY AUTOINCREMENT, "
                           "idempotency_key TEXT NOT NULL, task_id TEXT NOT NULL, revision INTEGER NOT NULL)")

    def visible(self) -> list[tuple]:
        return self._conn.execute("SELECT idempotency_key, task_id, revision FROM visible ORDER BY n").fetchall()

    def close(self) -> None:
        self._conn.close()


class IdempotentSink(_Sink):
    supports_idempotency = True

    def __init__(self, path):
        super().__init__(path)
        self._conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS visible_key ON visible(idempotency_key)")

    def publish(self, revision: TaskRevision) -> str:
        self._conn.execute("INSERT OR IGNORE INTO visible (idempotency_key, task_id, revision) VALUES (?,?,?)",
                           (revision.idempotency_key, revision.task_id, revision.revision))
        return f"receipt:{revision.idempotency_key[:12]}"


class AtLeastOnceSink(_Sink):
    supports_idempotency = False

    def publish(self, revision: TaskRevision) -> str:
        self._conn.execute("INSERT INTO visible (idempotency_key, task_id, revision) VALUES (?,?,?)",
                           (revision.idempotency_key, revision.task_id, revision.revision))
        return "receipt:no-idempotency"
