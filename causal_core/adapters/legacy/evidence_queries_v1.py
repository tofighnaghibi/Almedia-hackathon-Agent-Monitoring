"""v1 EvidenceQueryPort calls → typed v2.0 CapabilityRequests (v3.2 §2.4, F06).

v2.1 promised "exactly 6 capabilities" but only ever named four (inventory
D1, approved resolution). Those four are mapped here; anything else is
refused with a pointer to D1 rather than guessed. Semantics kept from v2.1:
`bucket_grain=None` means the v1.0 single-window breakdown, and `max_buckets`
defaults to 168.
"""
from __future__ import annotations

import json

from causal_core.api import ContractError
from causal_core.contracts.capabilities import CapabilityRequest
from pydantic import TypeAdapter, ValidationError

LEGACY_METHODS = ("fetch_metric_history", "fetch_segment_breakdown", "fetch_interventions", "fetch_experiments")
_REQUEST = TypeAdapter(CapabilityRequest)


class UnknownLegacyMethod(ValueError):
    pass


def to_typed_request(method: str, *, request_id: str, scope: dict, data_cut: str, **kw) -> CapabilityRequest:
    common = {"schema_version": "2.0", "request_id": request_id, "scope": scope, "data_cut": data_cut,
              "purpose": f"legacy_v1:{method}"}
    if kw.get("window"):
        common["window"] = kw["window"]
    max_buckets = (kw.get("budget") or {}).get("max_buckets", 168)
    if method == "fetch_metric_history":
        wire = {**common, "request": "get_series", "variables": list(kw["metric_ids"]),
                "grain": kw["grain"], "max_buckets": max_buckets}
    elif method == "fetch_segment_breakdown":
        wire = {**common, "request": "get_strata", "variables": [kw["metric_id"]], "dimension": kw["dimension"],
                "top_k": kw.get("top_k"), "include_other": True, "max_buckets": max_buckets}
        if kw.get("bucket_grain") is not None:
            wire["bucket_grain"] = kw["bucket_grain"]
        if wire["top_k"] is None:
            del wire["top_k"]
    elif method == "fetch_interventions":
        wire = {**common, "request": "get_intervention_history", "variables": list(kw["metric_ids"])}
    elif method == "fetch_experiments":
        if not kw.get("study_id"):
            raise UnknownLegacyMethod("v1 fetch_experiments without a study_id has no typed equivalent; "
                                      "discover studies via get_source_metadata first")
        wire = {**common, "request": "get_assignment_outcomes", "variables": list(kw["metric_ids"]),
                "study_id": kw["study_id"], "include_covariance": bool(kw.get("include_covariance", False))}
    else:
        raise UnknownLegacyMethod(f"{method!r} is not one of the four named v1 methods {LEGACY_METHODS} "
                                  "(v2.1 promised six; two were never specified, inventory D1)")
    try:
        return _REQUEST.validate_json(json.dumps(wire))   # same strict wire path as the core
    except ValidationError as exc:
        raise ContractError("SCHEMA_INVALID", [str(e["msg"]) for e in exc.errors()]) from None
