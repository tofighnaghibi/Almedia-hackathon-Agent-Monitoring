# Phase 1 Audit — Approval Request

**Checkpoint A verdict (audit): `PASS_WITH_NONBLOCKING_FOLLOWUPS`, provisional.**

**User review 2026-11-22: `APPROVED_BY_USER`** — CP-002, CP-003, CP-004 and CP-005 approved in batch (BEFORE: provisional, as recorded below).
- **Prompt vs protocol:** under the audit prompt alone this would read `BLOCKED_PENDING_APPROVAL`, because four
  Class B decisions were made after your checkpoint A approval. The run protocol tells me to implement such
  decisions provisionally and continue, so Phase 2 proceeds. Its code depends only on CP-005 (entity/dimension
  specs) and stays decoupled from CP-002, CP-003 and CP-004.
- **No approval assumed:** your approval of `38a5081` does not cover any of the items below.

| # | Decision | Options | Recommendation (implemented provisionally) | Evidence | Your action |
|---|---|---|---|---|---|
| 1 | CP-002 `schema_version` on native outputs; manifest latency/cost | required / defaulted / envelope | Required field; latency/cost optional with None = UNKNOWN | `NativeOutputsCP002` | Approve, switch, or revert (tag `checkpoint/CP-002-…`) |
| 2 | CP-003 UTC on the wire | reject / normalize / accept any | Reject non-UTC; adapters convert | `UtcOnTheWireCP003`, corpus D4 | Approve, switch, or revert |
| 3 | CP-004 estimand scopes | both required / optional / inherit | Both required; `requires_transport` | `EstimandScopesCP004`, corpus L5 | Approve, switch, or revert |
| 4 | CP-005 `EntitySpec` / `DimensionSpec` | dedicated / reuse categorical / defer | Dedicated, with explicit `partition_policy` | `EntityDimensionCP005` | Approve, switch, or revert |
| 5 | F-14 (Class E): `ActionProposal` is in v3.2 §2.1 but not in plan §C.4 | Phase 1 now / Phase 5 with the policy gate | Phase 5 (no consumer before the policy gate) | future-007 | Confirm |
| 6 | Re-approve checkpoint A on the audited state (Tier 1 fixes `ca8bf70` + items 1–4) | — | — | `VALIDATION.md` (60 tests, 43/43 corpus) | Approve or request changes |
