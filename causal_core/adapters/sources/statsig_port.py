"""Read-only Statsig adapter (outside the core; CP-033).

Translates Statsig Console API data into the core's contracts:
  * experiment details  → a RandomizedDesign (registered, attested by this adapter: CP-023)
  * Pulse results       → per-arm AggregateStats observations (n = units, mean, se = Pulse "Std", which is the
                           standard error of the arm mean), segment ((arm, <group>),)

What it never does:
  * write anything: the client only issues GET (a non-GET call raises before any network access);
  * guess the contrast: the control is the group Statsig marks `isControl`; with more than two groups the caller
    must declare which two to compare, otherwise the request is UNSUPPORTED;
  * trust Statsig's computed results: the deltas, CIs and p-values are kept aside (`source_reported`) only for a
    cross-check; the core recomputes SRM and the effect from the arm counts, means and SEs (D6);
  * invent availability: `available_at` is the moment the adapter retrieved the data (a KNOWN upper bound), so a task
    whose data cut is earlier sees nothing.

Two transports: live (STATSIG_CONSOLE_TOKEN, https://statsigapi.net/console/v1) and an offline cache directory laid
out as <dir>/experiments/<id>/{details,pulse_cuped}.json with a cache_manifest.json (the hackathon cache format).
A cached Pulse file does not say which two groups it compared; the adapter applies the rule the cache was built with
and refuses other contrasts (found when the SRM check refused a cached result whose arms had been swapped).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from causal_core.api import parse_observation
from causal_core.contracts.capabilities import CapabilityResponse, ProviderManifest
from causal_core.contracts.common import EnvironmentScope

SOURCE = "statsig-console"
SCOPE = {"environment_id": "statsig-babbel-production", "environment_kind": "production"}
BASE_URL = "https://statsigapi.net/console/v1"
FINISHED = {"decision_made", "archived", "launched", "abandoned"}
CUPED_MECHANISM = "variance_adjusted_by_source:cuped"


def slug(text: str) -> str:
    """A source label as a core Id: [A-Za-z0-9_.:-/], starting alphanumeric. The original label is kept elsewhere."""
    s = re.sub(r"[^A-Za-z0-9_.:\-/]+", "_", text.strip()).strip("_")
    return s if s and s[0].isalnum() else f"x{s}"


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class ReadOnlyStatsigClient:
    """GET-only access to the Console API, or to an offline cache directory."""

    def __init__(self, token: str | None = None, *, cache_dir: str | Path | None = None, timeout_s: float = 20.0):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.token = token if token is not None else os.environ.get("STATSIG_CONSOLE_TOKEN")
        if self.cache_dir is None and not self.token:
            raise ValueError("no STATSIG_CONSOLE_TOKEN and no cache_dir: nothing to read from")
        self.timeout_s = timeout_s
        self.calls: list[str] = []

    def _get(self, path: str, params: dict | None = None) -> dict:
        return self.request("GET", path, params)

    def request(self, method: str, path: str, params: dict | None = None) -> dict:
        if method != "GET":
            raise PermissionError(f"read-only adapter: {method} is not allowed")
        self.calls.append(path)
        import requests
        r = requests.get(f"{BASE_URL}/{path.strip('/')}", headers={"STATSIG-API-KEY": self.token}, params=params,
                         timeout=self.timeout_s)
        r.raise_for_status()
        body = r.json()
        return body.get("data", body)

    def _cached(self, experiment_id: str, kind: str) -> tuple[dict, datetime]:
        p = self.cache_dir / "experiments" / experiment_id / f"{kind}.json"
        if not p.exists():
            raise FileNotFoundError(f"{experiment_id}: no cached {kind}")
        manifest = json.loads((self.cache_dir / "cache_manifest.json").read_text()).get("entries", {}) \
            if (self.cache_dir / "cache_manifest.json").exists() else {}
        stamp = manifest.get(f"experiments/{experiment_id}/{kind}", {}).get("cached_at")
        when = datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc) if stamp else \
            datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        return json.loads(p.read_text()), when

    def experiment(self, experiment_id: str) -> tuple[dict, datetime]:
        if self.cache_dir:
            return self._cached(experiment_id, "details")
        return self._get(f"experiments/{experiment_id}"), datetime.now(timezone.utc)

    def pulse(self, experiment_id: str, control_id: str, test_id: str, cuped: bool = True) -> tuple[dict, datetime]:
        if self.cache_dir:
            return self._cached(experiment_id, f"pulse_{'cuped' if cuped else 'raw'}")
        return (self._get(f"experiments/{experiment_id}/pulse_results",
                          {"control": control_id, "test": test_id, "cuped": str(cuped).lower()}), datetime.now(timezone.utc))


def design_from_statsig(details: dict) -> dict:
    """Experiment details → RandomizedDesign wire dict. Only what Statsig states; unknowns stay 'unknown'."""
    groups = [g for g in details.get("groups", []) if not g.get("disabled")]
    total = sum(float(g.get("size", 0)) for g in groups)
    if not groups or total <= 0:
        raise ValueError(f"{details.get('id')}: no enabled groups with sizes")
    metrics = details.get("primaryMetrics", []) + details.get("secondaryMetrics", [])
    return {"schema_version": "2.0", "design": "randomized", "study_id": slug(details["id"]), "scope": SCOPE,
            "provenance": {"source_id": SOURCE, "method": "adapter_declared"},
            "population": f"Statsig {details.get('idType', 'unit')} units in experiment {details['id']}",
            "outcomes": [slug(f"{m['name']}::{m.get('type', '')}") for m in metrics] or ["unspecified"],
            "randomization": "simple", "assignment_unit": slug(details.get("idType") or "unit"),
            "outcome_unit": slug(details.get("idType") or "unit"),
            "expected_allocation": {slug(g["name"]): float(g["size"]) / total for g in groups},
            "compliance": "unknown", "interference": "unknown"}


class StatsigEvidencePort:
    """EvidenceAccessPort over Statsig Pulse. Supported request: get_assignment_outcomes (per-arm summaries)."""

    SUPPORTED = ("get_assignment_outcomes",)

    def __init__(self, client: ReadOnlyStatsigClient, *, contrasts: dict[str, tuple[str, str]] | None = None,
                 cuped: bool = True, provider_id: str = "statsig-pulse-adapter", exposure_counts=None):
        """exposure_counts: optional callable (experiment_id, [group names]) -> {group name: assigned units}, e.g. a
        read-only query of the warehouse exposures table. Without it, assignment counts are the largest per-group unit
        count across the experiment's Pulse metrics (the Console API has no per-group exposure count), labelled so."""
        self.client, self.cuped, self.provider_id = client, cuped, provider_id
        self.exposure_counts = exposure_counts
        self.contrasts = contrasts or {}               # experiment id → (control group name, test group name)
        self.source_reported: dict[tuple[str, str], dict] = {}

    def manifest(self) -> ProviderManifest:
        return ProviderManifest(schema_version="2.0", provider_id=self.provider_id, manifest_version="1",
                                supported_requests=self.SUPPORTED, max_rows=10_000,
                                scopes=(EnvironmentScope(**SCOPE),),
                                expected_latency_ms=None, cost_units_per_request=None)   # unknown: penalised, not free

    def _resp(self, request, status: str, reason: str | None = None, obs=()) -> CapabilityResponse:
        fp = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        return CapabilityResponse(schema_version="2.0", request_id=request.request_id, request_fingerprint=fp,
                                  status=status, reason=reason, observations=tuple(obs))

    def _contrast(self, exp_id: str, groups: list[dict]) -> tuple[dict, dict] | str:
        by_name = {g["name"]: g for g in groups}
        if self.client.cache_dir is not None:
            # A cached Pulse file does not record which groups it compared. The hackathon cache was filled with the
            # rule "control = first group whose name contains 'control', else the first group; test = the next one"
            # (hackathon/statsig_experiment_api.py). Use exactly that pair, and refuse any other contrast.
            ctrl = next((g for g in groups if "control" in g["name"].lower()), groups[0] if groups else None)
            test = next((g for g in groups if g is not ctrl), None)
            if ctrl is None or test is None:
                return "cached experiment has fewer than two groups"
            if exp_id in self.contrasts and tuple(self.contrasts[exp_id]) != (ctrl["name"], test["name"]):
                return (f"the cached Pulse compares {test['name']} vs {ctrl['name']}; another contrast needs a live call")
            return ctrl, test
        if exp_id in self.contrasts:
            c, t = self.contrasts[exp_id]
            if c not in by_name or t not in by_name:
                return f"declared contrast {self.contrasts[exp_id]} not among groups {sorted(by_name)}"
            return by_name[c], by_name[t]
        if len(groups) != 2:
            return f"{len(groups)} groups ({sorted(by_name)}): declare which two to compare"
        ctrl = [g for g in groups if g.get("isControl")]
        if len(ctrl) != 1:
            return "Statsig marks no single control group: declare the contrast"
        return ctrl[0], next(g for g in groups if g is not ctrl[0])

    def query(self, request) -> CapabilityResponse:
        if request.request not in self.SUPPORTED:
            return self._resp(request, "UNSUPPORTED", f"{request.request} not offered by {self.provider_id}")
        if request.scope.environment_id != SCOPE["environment_id"]:
            return self._resp(request, "UNSUPPORTED", f"this adapter serves {SCOPE['environment_id']} only")
        details, _ = self.client.experiment(request.study_id)
        groups = [g for g in details.get("groups", []) if not g.get("disabled")]
        pair = self._contrast(request.study_id, groups)
        if isinstance(pair, str):
            return self._resp(request, "UNSUPPORTED", pair)
        control, test = pair
        pulse, retrieved = self.client.pulse(request.study_id, control["id"], test["id"], self.cuped)
        if retrieved > request.data_cut:
            return self._resp(request, "OK", None, ())          # retrieved after the data cut: not provably available
        metrics = pulse.get("primaryMetrics", []) + pulse.get("secondaryMetrics", [])
        ds = datetime.fromisoformat(pulse["ds"]).replace(tzinfo=timezone.utc) + timedelta(days=1)
        start = datetime.fromtimestamp(int(details["startTime"]) / 1000, tz=timezone.utc)
        mature = details.get("status") in FINISHED
        obs, missing = [], []
        for var in request.variables:
            m = next((m for m in metrics if var in (slug(m["metricID"]), slug(m.get("metricName", "")))), None)
            if m is None:
                missing.append(var)
                continue
            self.source_reported[(request.study_id, var)] = {
                k: m.get(k) for k in ("absoluteChange", "confidenceInterval", "adjustedAlpha", "pValue", "metricName")}
            for g, side in ((test, "test"), (control, "control")):
                payload = {"shape": "aggregate_stats", "window": {"start": _iso(start), "end": _iso(ds)},
                           "n": int(m[f"{side}Units"]), "mean": float(m[f"{side}Mean"]), "se": float(m[f"{side}Std"]),
                           "iid": "ASSERTED_BY_SOURCE"}
                key = f"{request.study_id}:{var}:{slug(g['name'])}:{pulse['ds']}"
                h = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
                obs.append(parse_observation({
                    "schema_version": "2.0", "observation_id": slug(f"statsig:{key}"), "variable_id": var,
                    "payload": payload, "source_id": SOURCE, "record_key": slug(key), "revision": 1, "scope": SCOPE,
                    "lineage": {"source_id": SOURCE, "origin_kind": "original", "lineage_root_id": slug(f"{SOURCE}:{key}"),
                                "content_hash": h},
                    "clocks": {"observed_at": {"quality": "KNOWN", "value": _iso(ds)},
                               "available_at": {"quality": "KNOWN", "value": _iso(retrieved)}},
                    "dq": {"maturity": "MATURE" if mature else "IMMATURE"},
                    "observation_mechanisms": [CUPED_MECHANISM] if self.cuped else [],
                    "segment": [{"dimension_id": "arm", "value": slug(g["name"])}]}))
        if not obs:
            return self._resp(request, "UNSUPPORTED", f"metric(s) {missing} not in this experiment's Pulse results")
        # assignment counts for SRM (CP-032 fix)
        if self.exposure_counts is not None:
            counts = self.exposure_counts(request.study_id, [control["name"], test["name"]])
            how = "assignment_count:warehouse_exposures"
        else:
            counts = {test["name"]: max(int(x["testUnits"]) for x in metrics if x.get("testUnits") is not None),
                      control["name"]: max(int(x["controlUnits"]) for x in metrics if x.get("controlUnits") is not None)}
            how = "assignment_count:max_units_across_pulse_metrics"
        for g in (test, control):
            payload = {"shape": "aggregate_stats", "n": int(counts[g["name"]]), "total": float(counts[g["name"]])}
            key = f"{request.study_id}:assignment:{slug(g['name'])}:{pulse['ds']}"
            obs.append(parse_observation({
                "schema_version": "2.0", "observation_id": slug(f"statsig:{key}"), "variable_id": "assignment_units",
                "payload": payload, "source_id": SOURCE, "record_key": slug(key), "revision": 1, "scope": SCOPE,
                "lineage": {"source_id": SOURCE, "origin_kind": "original", "lineage_root_id": slug(f"{SOURCE}:{key}"),
                            "content_hash": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()},
                "clocks": {"observed_at": {"quality": "KNOWN", "value": _iso(ds)},
                           "available_at": {"quality": "KNOWN", "value": _iso(retrieved)}},
                "observation_mechanisms": [how], "segment": [{"dimension_id": "arm", "value": slug(g["name"])}]}))
        return self._resp(request, "PARTIAL" if missing else "OK",
                          f"metric(s) {missing} not in Pulse results" if missing else None, obs)
