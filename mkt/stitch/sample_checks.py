"""Checks every sample against its Contracts definition. Returns a list of problems (empty = pass)."""
from __future__ import annotations

from causal_core.contracts.evidence import EvidenceRecord
from causal_core.contracts.observations import ALLOWED_SHAPES, Observation

TYPES = {"real": (float, int), "integer": (int,), "boolean": (bool,), "text": (str,), "category": (str,)}
BANNED = ("fraud_label", "true_late", "latent", "flag_state", "twin", "oracle", "answer.json", "paymentfailure")


def check_sample(var, definition, o: Observation, e: EvidenceRecord) -> list[str]:
    p = []
    # 1. strict re-validation from JSON (it is a genuine Contracts object)
    if Observation.model_validate_json(o.model_dump_json()) != o:
        p.append("observation does not round-trip")
    if EvidenceRecord.model_validate_json(e.model_dump_json()) != e:
        p.append("evidence does not round-trip")
    # 2. shape allowed for the variable's kind
    if o.payload.shape not in ALLOWED_SHAPES[var.kind]:
        p.append(f"shape {o.payload.shape} not allowed for kind {var.kind}")
    # 3. value-level conformance to the definition
    vs = definition.value_schema
    if var.kind == "metric":
        vals = [x.value for x in o.payload.points if x.value is not None] if o.payload.shape == "time_series" else \
               [u.outcome for u in o.payload.units if u.outcome is not None] if o.payload.shape == "assignment_outcomes" else []
        if vs.dtype == "proportion" and any(not 0 <= v <= 1 for v in vals):
            p.append("proportion outside [0, 1]")
        if vs.dtype == "count" and any(v < 0 or v != int(v) for v in vals):
            p.append("count not a non-negative integer")
        if vs.minimum is not None and any(v < vs.minimum for v in vals):
            p.append("value below declared minimum")
    if var.kind == "event":
        fields = vs.fields
        for ev in o.payload.events:
            if set(ev.attributes) != set(fields):
                p.append(f"event fields {sorted(ev.attributes)} != declared {sorted(fields)}")
                break
            bad = [k for k, val in ev.attributes.items() if val is not None and not (isinstance(val, TYPES[fields[k]])
                                                                                     and not (fields[k] != "boolean" and isinstance(val, bool)))]
            if bad:
                p.append(f"event field types wrong: {bad}")
                break
            if ev.attributes.get(vs.identity_fields[0]) is None:
                p.append("identity field missing")
                break
    if var.kind in ("categorical", "state") and getattr(vs, "categories", None):
        allowed = set(vs.categories)
        got = [o.payload.category] if o.payload.shape == "categorical" else \
              [s.state for s in o.payload.samples] if o.payload.shape == "state_trajectory" else []
        if any(g not in allowed for g in got):
            p.append(f"category outside declared list: {sorted(set(got) - allowed)[:3]}")
    if var.kind == "state" and getattr(vs, "type", "") == "numeric" and o.payload.shape == "state_trajectory":
        if any(not isinstance(s.state, (int, float)) for s in o.payload.samples):
            p.append("numeric state carries text")
    # 4. clocks, scope, provenance
    c = o.clocks
    if c and c.observed_at and c.available_at and c.available_at.quality == "KNOWN" and c.available_at.value < c.observed_at.value:
        p.append("available_at before observed_at")
    if c and c.available_at and c.as_of and c.available_at.value > c.as_of:
        p.append("available after the data cut")
    if o.lineage.source_id != o.source_id or e.lineage != o.lineage or e.scope != o.scope:
        p.append("lineage/scope mismatch between observation and evidence")
    if o.scope.environment_kind == "emulator" and e.provenance_role == "REAL_OBSERVED":
        p.append("emulator evidence labelled REAL_OBSERVED")
    # 5. no privileged ground truth in public samples
    blob = o.model_dump_json().lower()
    if any(b in blob for b in BANNED):
        p.append("privileged word in sample")
    return p
