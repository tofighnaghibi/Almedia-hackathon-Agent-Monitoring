-- Causal Core single-writer ledger (v3.2 §5.1; plan §E.1; CP-011).
-- The graph slice (graph_versions, graph_deltas, source_records) is created by graph_log.SCHEMA.
-- Every table is append-only or state-machine updated under BEGIN IMMEDIATE by one writer.

-- Evidence: immutable, content-addressed; (source, record_key, revision) is the identity (§2.2).
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id      TEXT PRIMARY KEY,
    source_id        TEXT NOT NULL,
    record_key       TEXT NOT NULL,
    revision         INTEGER NOT NULL CHECK (revision >= 1),
    variable_id      TEXT NOT NULL,
    content_hash     TEXT NOT NULL,
    lineage_root     TEXT NOT NULL,
    origin_kind      TEXT NOT NULL,
    provenance_role  TEXT NOT NULL CHECK (provenance_role <> 'MODEL_PREDICTION'),
    environment_id   TEXT NOT NULL,
    available_at     TEXT,              -- NULL unless the availability clock is KNOWN
    observation_json TEXT NOT NULL,
    recorded_seq     INTEGER NOT NULL,
    UNIQUE (source_id, record_key, revision)
);
CREATE INDEX IF NOT EXISTS evidence_by_variable ON evidence(variable_id, available_at);

-- Ancestry DAG: parents must already exist, so the graph is acyclic by construction.
CREATE TABLE IF NOT EXISTS lineage_parents (
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
    parent_id   TEXT NOT NULL REFERENCES evidence(evidence_id),
    PRIMARY KEY (evidence_id, parent_id)
);

-- Conflicting equal revisions and other refused records: kept, never silently dropped.
CREATE TABLE IF NOT EXISTS evidence_quarantine (
    source_id    TEXT NOT NULL,
    record_key   TEXT NOT NULL,
    revision     INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    reason       TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (source_id, record_key, revision, content_hash)
);

-- Model outputs never enter the evidence table (I11).
CREATE TABLE IF NOT EXISTS model_outputs (
    prediction_id   TEXT PRIMARY KEY,
    model_id        TEXT NOT NULL,
    model_version   TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    recorded_seq    INTEGER NOT NULL
);

-- Sequential monitoring state, one row per stream (method-specific; §5.1, §5.4).
CREATE TABLE IF NOT EXISTS eprocess_state (
    stream_key        TEXT PRIMARY KEY,
    spec_json         TEXT NOT NULL,     -- declared null, betting rule, alpha, method version
    consumed_json     TEXT NOT NULL,     -- bucket -> (record id, revision, lineage root, z)
    log_wealth        REAL NOT NULL,
    peak_log_wealth   REAL NOT NULL,
    alarm_events_json TEXT NOT NULL,     -- append-only: raised / superseded, never erased
    updated_seq       INTEGER NOT NULL
);

-- Method applicability decisions, including refusals (plan §E.7).
CREATE TABLE IF NOT EXISTS method_decisions (
    task_id        TEXT NOT NULL,
    task_revision  INTEGER NOT NULL,
    seq            INTEGER NOT NULL,
    method_id      TEXT NOT NULL,
    method_version TEXT NOT NULL,
    applicability  TEXT NOT NULL CHECK (applicability IN ('SUPPORTED','SUPPORTED_WITH_LIMITATIONS','UNSUPPORTED')),
    reasons_json   TEXT NOT NULL,
    seed           INTEGER,
    PRIMARY KEY (task_id, task_revision, seq)
);

-- Task revisions are monotone and unique; the outbox row commits in the same transaction.
CREATE TABLE IF NOT EXISTS task_revisions (
    task_id         TEXT NOT NULL,
    revision        INTEGER NOT NULL CHECK (revision >= 1),
    supersedes      INTEGER,
    outcome         TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_json    TEXT NOT NULL,
    PRIMARY KEY (task_id, revision)
);
CREATE TABLE IF NOT EXISTS outbox (
    outbox_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT NOT NULL,
    revision        INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE REFERENCES task_revisions(idempotency_key),
    payload_json    TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('PENDING','SENT','ACKED')),
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    FOREIGN KEY (task_id, revision) REFERENCES task_revisions(task_id, revision)
);

-- Durable jobs and leases (§5.2).
CREATE TABLE IF NOT EXISTS jobs (
    job_id                 TEXT PRIMARY KEY,
    task_id                TEXT NOT NULL,
    task_revision          INTEGER NOT NULL,
    tier                   TEXT NOT NULL CHECK (tier IN ('T0','T1','T2','T3','T4')),
    priority               INTEGER NOT NULL,
    state                  TEXT NOT NULL CHECK (state IN ('QUEUED','LEASED','RUNNING','CHECKPOINTED','COMPLETED','FAILED','CANCELLED')),
    attempt                INTEGER NOT NULL DEFAULT 0,
    max_attempts           INTEGER NOT NULL,
    deadline_at            REAL NOT NULL,
    lease_owner            TEXT,
    lease_until            REAL,
    retry_after            REAL NOT NULL DEFAULT 0,
    cancel_requested       INTEGER NOT NULL DEFAULT 0,
    remaining_query_budget INTEGER NOT NULL CHECK (remaining_query_budget >= 0),
    pinned_graph_version   INTEGER NOT NULL,
    idempotency_key        TEXT NOT NULL UNIQUE,
    last_error             TEXT,
    CHECK ((state IN ('LEASED','RUNNING')) = (lease_owner IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(state, tier, priority DESC, deadline_at);
CREATE TABLE IF NOT EXISTS checkpoints (
    job_id        TEXT NOT NULL REFERENCES jobs(job_id),
    seq           INTEGER NOT NULL,
    stage         TEXT NOT NULL,
    payload_hash  TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    queries_spent INTEGER NOT NULL,
    PRIMARY KEY (job_id, seq)
);

-- Query ledger: a charge is recorded BEFORE dispatch; a retry reuses the response (§5.2, plan §E.4).
CREATE TABLE IF NOT EXISTS query_ledger (
    request_fingerprint TEXT PRIMARY KEY,
    job_id              TEXT NOT NULL REFERENCES jobs(job_id),
    request_json        TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ('ISSUED','RESPONDED')),
    dispatch_count      INTEGER NOT NULL DEFAULT 0,
    charged_units       INTEGER NOT NULL,
    response_json       TEXT
);

-- Response cache keyed by scope + data cut + versions + content (§5.4).
CREATE TABLE IF NOT EXISTS query_cache (
    cache_key     TEXT PRIMARY KEY,
    response_json TEXT NOT NULL
);
