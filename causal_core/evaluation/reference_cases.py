"""The eleven Phase 0 reference cases (plan §B task 3, plus C01 noncompliance).

Each case has:
  build()  -> Built(input, table, truth, source)   visible input + hidden truth
  derive(input, rows) -> {"expected": ..., "v21_reference": ..., ...}

``derive`` only ever sees what a fixture stores (input JSON + data CSV), never
the truth. The freeze script writes its output as the frozen expectation;
the tests re-derive from the stored files to detect drift. ``behavior``
records how v3.2 differs from v2.1:
  PRESERVED  same conclusion as v2.1
  CORRECTED  v3.2 intentionally changes the conclusion (needs sign-off)
  CLARIFIED  v2.1 left it unspecified; v3.2 fills the gap
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from emulator import scenarios
from emulator.scenarios import Table
from evaluation import oracle


@dataclass(frozen=True)
class Built:
    input: dict
    table: Table | None = None
    truth: dict | None = None
    source: str = "hand_derived"


@dataclass(frozen=True)
class ReferenceCase:
    case_id: str
    title: str
    tests_what: str
    refs: tuple[str, ...]
    gates: tuple[str, ...]
    build: Callable[[], Built]
    derive: Callable[[dict, list[dict] | None], dict]
    behavior: dict = field(default_factory=dict)


# ── 01 ratio identity ──────────────────────────────────────────────────────

_RATIO_ROWS = [
    {"bucket": "2026-11-01", "numerator": 50, "denominator": 1000, "reported": 0.05},
    {"bucket": "2026-11-02", "numerator": 66, "denominator": 1200, "reported": 0.055},
    {"bucket": "2026-11-03", "numerator": 36, "denominator": 800, "reported": 0.045},
    {"bucket": "2026-11-04", "numerator": 0, "denominator": 0, "reported": None},
]


def _build_01() -> Built:
    drifted = [dict(r) for r in _RATIO_ROWS]
    drifted[2]["reported"] = 0.0475  # an undocumented upstream filter
    return Built({
        "variable": {"variable_id": "conversion_rate", "kind": "metric",
                     "formula": "div(orders, sessions)", "unit": "orders/session",
                     "time_grain": "1d", "population": "all_web_sessions"},
        "tolerance": 1e-4, "observations": _RATIO_ROWS, "drifted_variant": drifted})


def _derive_01(inp: dict, _rows) -> dict:
    return {"expected": {
        "main": oracle.check_ratio_identity(inp["observations"], inp["tolerance"]),
        "drifted_variant": oracle.check_ratio_identity(inp["drifted_variant"], inp["tolerance"]),
        "claim_kind": "MATHEMATICAL_IDENTITY", "causal": False}}


# ── 02 Simpson / Kitagawa ──────────────────────────────────────────────────

_SEGMENTS = [
    {"segment": "mobile", "den0": 2000, "num0": 40, "den1": 8000, "num1": 200},
    {"segment": "desktop", "den0": 8000, "num0": 480, "den1": 2000, "num1": 130},
]


def _build_02() -> Built:
    return Built({"metric": "conversion_rate = orders / sessions", "segments": _SEGMENTS,
                  "variant_missing_segment": {
                      "declared_totals": {"den0": 11000, "den1": 11000},
                      "note": "1,000 sessions per period from a segment the source did not report"}})


def _derive_02(inp: dict, _rows) -> dict:
    return {"expected": {
        "main": oracle.kitagawa(inp["segments"]),
        "variant_missing_segment": oracle.kitagawa(
            inp["segments"], inp["variant_missing_segment"]["declared_totals"]),
        "claim_limits": ["mix contribution is arithmetic, not evidence the change is harmless",
                         "aggregate impact stays visible; suppression needs an explicit policy"]}}


# ── 03 DQ reporting lag + replay leakage ───────────────────────────────────


def _build_03() -> Built:
    return Built({
        "variable": {"variable_id": "orders_daily", "kind": "metric", "time_grain": "1d"},
        "as_of": "2026-11-21T02:00:00+00:00", "maturity_threshold": 0.95,
        "buckets": [
            {"bucket": "2026-11-17", "value": 1000, "completeness": 1.0},
            {"bucket": "2026-11-18", "value": 1020, "completeness": 1.0},
            {"bucket": "2026-11-19", "value": 990, "completeness": 0.99},
            {"bucket": "2026-11-20", "value": 640, "completeness": 0.62},
        ],
        "replay_time": "2026-11-20T12:00:00+00:00",
        "observations": [
            {"id": "o1", "observed_at": "2026-11-20T10:00:00+00:00",
             "available_at": "2026-11-20T11:00:00+00:00", "available_at_quality": "KNOWN"},
            {"id": "o2", "observed_at": "2026-11-20T10:30:00+00:00",
             "available_at": "2026-11-21T01:00:00+00:00", "available_at_quality": "KNOWN"},
            {"id": "o3", "observed_at": "2026-11-20T09:00:00+00:00",
             "available_at": None, "available_at_quality": "UNKNOWN"},
            {"id": "o4", "observed_at": "2026-11-20T11:30:00+00:00",
             "available_at": "2026-11-20T11:45:00+00:00", "available_at_quality": "ESTIMATED"},
        ]})


def _derive_03(inp: dict, _rows) -> dict:
    dq = oracle.dq_maturity(inp["buckets"], inp["maturity_threshold"])
    replay = oracle.replay_visibility(inp["observations"], inp["replay_time"])
    return {
        "expected": {
            "dq": dq, "claim_kind_for_immature_bucket": "DQ_FAILURE",
            "domain_attribution_for_immature_bucket": "BLOCKED_UNTIL_MATURE",
            "independent_business_risk_check": "RETAINED_NOT_SUPPRESSED",
            "replay_visible": replay["visible"], "replay_hidden": replay["hidden"],
            "replay_refused": replay["refused"]},
        "v21_reference": {
            "dq": "completeness < 0.95 -> DQ_HALT (same)",
            "replay_rule": "event_time/as_of only",
            "would_show": replay["observed_at_only_rule_would_show"],
            "look_ahead_leak": replay["leak_under_observed_at_rule"],
            "unverifiable": replay["unverifiable_under_observed_at_rule"]}}


# ── 04 valid randomized study, no anomaly ──────────────────────────────────


def _build_04() -> Built:
    return Built({
        "question_kind": "ESTIMATE_EFFECT", "monitor": "ABSENT",
        "study_design": {"design": "randomized", "assignment_unit": "user",
                         "outcome_unit": "user", "analysis_unit": "user",
                         "expected_allocation": {"treatment": 0.5, "control": 0.5},
                         "compliance": "assignment_is_exposure"},
        "estimand": {"outcome": "conversion_7d", "contrast": "assigned treatment vs control",
                     "population": "users randomized 2026-11-01..14 in env:staging-web",
                     "horizon": "7d", "functional": "ITT risk difference"},
        "assigned": {"treatment": 9960, "control": 10040},
        "converted": {"treatment": 1096, "control": 1004}})


def _derive_04(inp: dict, _rows) -> dict:
    a, c = inp["assigned"], inp["converted"]
    return {
        "expected": {
            "srm_at_assignment_unit": oracle.srm_two_arm(a, inp["study_design"]["expected_allocation"]),
            "itt": oracle.diff_in_proportions(c["treatment"], a["treatment"], c["control"], a["control"]),
            "identification": "CONDITIONAL_IDENTIFICATION",
            "claim_kind": "CAUSAL_EFFECT_CONDITIONAL",
            "graph_artifact": "EffectClaim(assignment -> conversion_7d)",
            "structural_edge_admitted": False,
            "scope": inp["estimand"]["population"]},
        "v21_reference": {
            "srm": "read from ExperimentRecord.srm_p_value (source-supplied, not computed)",
            "output": "IdentificationCertificate -> propose CAUSALLY_IDENTIFIED edge exp -> conversion_7d"}}


# ── 05 cluster-randomized trial, invalid IID SE ────────────────────────────


def _build_05() -> Built:
    draw = scenarios.cluster_randomized_trial(seed=2026_11_05)
    return Built({
        "question_kind": "ASSESS_STUDY",
        "study_design": {"design": "randomized", "assignment_unit": "cluster",
                         "outcome_unit": "individual",
                         "analysis_unit_requested_by_source": "individual"},
        "estimand": {"outcome": "y", "contrast": "cluster assigned treatment vs control",
                     "functional": "ITT mean difference"}},
        draw.visible, draw.truth, "emulator:cluster_randomized_trial seed=20261105")


def _derive_05(_inp: dict, rows) -> dict:
    naive = oracle.naive_iid_difference([r["y"] for r in rows if r["arm"] == "treatment"],
                                        [r["y"] for r in rows if r["arm"] == "control"])
    cluster = oracle.cluster_level_analysis(rows)
    expected = {"naive_individual_analysis": "REFUSED: NAIVE_IID_SE_UNDER_CLUSTER_ASSIGNMENT",
                "cluster_analysis": cluster}
    if cluster["support"] == "UNSUPPORTED":
        expected["claim_kind"] = "INCONCLUSIVE"
        expected["refusal"] = "UNSUPPORTED_CLUSTER_INFERENCE"
    else:
        expected["claim_kind"] = "CAUSAL_EFFECT_CONDITIONAL"
        expected["empirical_variance_inflation"] = (cluster["se"] / naive["se"]) ** 2
    return {"expected": expected,
            "v21_reference": {"rule": "randomization unit must equal exposure/analysis unit",
                              "outcome": "study rejected as invalid; no cluster-aware path"},
            "failure_mode_if_unguarded": naive}


# ── 06 conditional DiD ─────────────────────────────────────────────────────


def _build_06() -> Built:
    draw = scenarios.parallel_trends_panel(seed=2026_11_06)
    return Built({
        "question_kind": "ESTIMATE_EFFECT",
        "study_design": {"design": "difference_in_differences", "treated_group": "treated",
                         "intervention_period": 0, "timing": "single (not staggered)"},
        "estimand": {"functional": "ATT", "population": "treated units", "horizon": "period 0"}},
        draw.visible, draw.truth, "emulator:parallel_trends_panel seed=20261106")


def _derive_06(_inp: dict, rows) -> dict:
    did = oracle.did_collapsed(rows)
    return {"expected": {
        **did, "claim_kind": "CAUSAL_EFFECT_CONDITIONAL",
        "untestable_assumptions": ["parallel trends in the post period", "no anticipation",
                                   "no spillover to controls", "stable group composition"],
        "pretrend_role": "diagnostic only; a pass is not proof of parallel trends",
        "structural_edge_admitted": False},
        "v21_reference": {"rule": "mandatory pre-trend falsification",
                          "outcome": "INTERVENTION_SUPPORTED -> HUMAN_APPROVAL_REQUIRED (same estimate)"}}


# ── 07 known unidentifiable confounder ─────────────────────────────────────


def _build_07() -> Built:
    draw = scenarios.latent_confounded_pair(seed=2026_11_07)
    return Built({
        "question_kind": "ESTIMATE_EFFECT",
        "estimand": {"treatment": "x", "outcome": "y", "functional": "ATE"},
        "study_design": {"design": "observational", "adjustment_set": [], "instruments": []},
        "declared_graph": [
            {"source": "x", "target": "y", "kind": "CAUSAL_HYPOTHESIS"},
            {"source": "x", "target": "y", "kind": "LATENT_CONFOUNDING", "status": "CANDIDATE",
             "note": "analyst-declared plausible unmeasured common cause"}]},
        draw.visible, draw.truth, "emulator:latent_confounded_pair seed=20261107")


def _derive_07(inp: dict, rows) -> dict:
    design = inp["study_design"]
    audit = oracle.audit_latent_backdoor(inp["declared_graph"], "x", "y", design["design"],
                                         design["adjustment_set"], design["instruments"])
    return {"expected": {
        "association": oracle.ols([r["x"] for r in rows], [r["y"] for r in rows]),
        "identification": audit, "claim_kind": "ASSOCIATION",
        "evidence_gaps": ["randomize x (or find a valid instrument)",
                          "measure a proxy for the latent common cause"]},
        "v21_reference": {"outcome": "INCONCLUSIVE_ABSTAINED (same)"}}


# ── 08 revised bucket + synthetic replicas in an e-process ─────────────────


def _build_08() -> Built:
    def rec(i, bucket, rev, root, z, origin="original"):
        return {"id": i, "bucket": bucket, "revision": rev, "lineage_root": root,
                "origin_kind": origin, "z": z}
    arrival = [rec("b1r1", "b1", 1, "root_b1", 0.3), rec("b2r1", "b2", 1, "root_b2", 1.1)]
    arrival += [rec(f"b2_replica_{k:02d}", "b2", 1, "root_b2", 1.1, "synthetic_replica")
                for k in range(50)]
    arrival += [rec("b3r1", "b3", 1, "root_b3", 5.0),   # partial bucket, looks extreme
                rec("b4r1", "b4", 1, "root_b4", 0.9),
                rec("b3r2", "b3", 2, "root_b3", 0.4)]   # correction arrives late
    return Built({"null": "z_t i.i.d. N(0,1)", "bet": "fixed lambda (predictable)",
                  "lambda": 0.5, "alpha": 0.05, "records_in_arrival_order": arrival})


def _derive_08(inp: dict, _rows) -> dict:
    recs, lam, alpha = inp["records_in_arrival_order"], inp["lambda"], inp["alpha"]
    return {
        "expected": {**oracle.eprocess_log_wealth(recs, lam, alpha),
                     "history_rule": "an alarm already published from b3r1 stays in history, "
                                     "superseded by a new revision; it is not erased"},
        "v21_reference": {"rule": "consume each lineage root once; 'reconcile as a revision' unspecified",
                          "first_seen_root_implementation": oracle.eprocess_first_seen_root(recs, lam, alpha)},
        "failure_mode_if_unguarded": oracle.eprocess_unguarded(recs, lam, alpha)}


# ── 09 migrated legacy records ─────────────────────────────────────────────

_LEGACY_OBS = ('{"schema_version":"1.1","kind":"Observation","metric_id":"m_conv",'
               '"window":{"start":"2026-10-01T00:00:00Z","end":"2026-10-02T00:00:00Z"},'
               '"stats":{"numerator":520,"denominator":10000,"n":10000,"se":0.00222},'
               '"event_time":"2026-10-01T23:59:59Z","as_of":"2026-10-02T06:00:00Z","revision_id":1}')
_LEGACY_HYP = ('{"schema_version":"1.1","kind":"GraphEdge","edge_kind":"HYPOTHESIZED_CAUSAL",'
               '"source":"m_ads","target":"m_conv"}')
_LEGACY_CERT = ('{"schema_version":"1.2","kind":"GraphEdge","edge_kind":"CAUSALLY_IDENTIFIED",'
                '"source":"exp_17","target":"m_conv","identification_certificate_id":"cert_17",'
                '"evidence_class":"EXPERIMENTAL"}')


def _build_09() -> Built:
    return Built({"raw_records": {"observation": _LEGACY_OBS, "hypothesis_edge": _LEGACY_HYP,
                                  "certified_edge": _LEGACY_CERT}})


def _derive_09(inp: dict, _rows) -> dict:
    raw = inp["raw_records"]
    receipt = {k: {"original_sha256": oracle.sha256_hex(v)} for k, v in raw.items()}
    return {"expected": {
        "receipts": receipt,
        "observation": {
            "schema_version": "2.0", "payload_kind": "AggregateStats",
            "preserved": {"numerator": 520, "denominator": 10000, "n": 10000, "se": 0.00222,
                          "window": ["2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"], "revision": 1},
            "not_inferred": {"iid": "UNKNOWN", "covariance": "UNKNOWN (never zero)"},
            "clocks": {"observed_at": "2026-10-01T23:59:59Z (as declared)",
                       "as_of": "2026-10-02T06:00:00Z", "available_at": "UNKNOWN"},
            "scope": "LEGACY_UNSCOPED",
            "lineage_root": oracle.legacy_lineage_root(raw["observation"]),
            "forbidden_uses": ["production claims", "action permissions", "leakage-sensitive replay"]},
        "hypothesis_edge": {"edge_kind": "CAUSAL_HYPOTHESIS", "certification_upgrade": False},
        "certified_edge": {"edge_kind": "CAUSALLY_IDENTIFIED (historical)",
                           "status": "REVIEW_REQUIRED",
                           "reasons": ["MISSING_ESTIMAND", "MISSING_ASSUMPTION_AUDIT", "MISSING_SCOPE"],
                           "authority_until_reaudit": "none"}},
        "v21_reference": {"observation": "LEGACY_UNSCOPED (same)",
                          "hypothesis_edge": "CAUSAL_HYPOTHESIS (same)",
                          "certified_edge": "kept as CAUSALLY_IDENTIFIED with full authority (implicit)"}}


# ── 10 unauthorized action ─────────────────────────────────────────────────


def _build_10() -> Built:
    attested = {"issuer": "sandbox-registry", "target": "sandbox-emu-eu-1"}
    return Built({
        "sandbox_allowlist": ["sandbox-emu-eu-1"], "evaluated_at": "2026-11-21T12:00:00+00:00",
        "proposals": [
            {"id": "A_spoofed_scope", "action_kind": "sandbox_test", "target": "prod-marketplace-eu",
             "environment_kind": "emulator", "environment_attestation": None,
             "approval": {"bound_target": "prod-marketplace-eu", "expires_at": "2026-11-22T00:00:00+00:00"}},
            {"id": "B_expired_approval", "action_kind": "sandbox_test", "target": "sandbox-emu-eu-1",
             "environment_kind": "emulator", "environment_attestation": attested,
             "approval": {"bound_target": "sandbox-emu-eu-1", "expires_at": "2026-11-21T00:00:00+00:00"}},
            {"id": "C_valid_sandbox_test", "action_kind": "sandbox_test", "target": "sandbox-emu-eu-1",
             "environment_kind": "emulator", "environment_attestation": attested,
             "approval": {"bound_target": "sandbox-emu-eu-1", "expires_at": "2026-11-22T00:00:00+00:00"}},
        ]})


def _derive_10(inp: dict, _rows) -> dict:
    when = inp["evaluated_at"]
    return {"expected": {p["id"]: oracle.policy_v32(p, inp["sandbox_allowlist"], when)
                         for p in inp["proposals"]},
            "v21_reference": {p["id"]: oracle.policy_v21_literal(p, when) for p in inp["proposals"]},
            "note": "C is allowed by policy but not executable before Phase 8: P0 has no write port"}


# ── 11 noncompliance: ITT is not an exposure edge ──────────────────────────


def _build_11() -> Built:
    draw = scenarios.one_sided_noncompliance(seed=2026_11_11)
    return Built({
        "question_kind": "ESTIMATE_EFFECT",
        "study_design": {"design": "randomized", "assignment_unit": "user", "assignment": "z",
                         "exposure_recorded": "d", "noncompliance": "one-sided (declared)"},
        "requested_claims": ["effect of z on y", "effect of d on y", "admit edge d -> y"]},
        draw.visible, draw.truth, "emulator:one_sided_noncompliance seed=20261111")


def _derive_11(_inp: dict, rows) -> dict:
    nc = oracle.noncompliance_analysis(rows)
    return {"expected": {
        **nc,
        "claims": {"z_on_y": "CAUSAL_EFFECT_CONDITIONAL (ITT, randomization)",
                   "d_on_y": "CAUSAL_EFFECT_CONDITIONAL only as a separate IV claim, scoped to compliers",
                   "edge_d_to_y_from_itt": {"admitted": False, "reason": "EXPOSURE_NOT_IDENTIFIED_BY_ITT"}}},
        "v21_reference": {"rule": "randomization/exposure unit alignment PASS -> certificate",
                          "outcome": "CAUSALLY_IDENTIFIED edge on the exposure path"}}


CASES: tuple[ReferenceCase, ...] = (
    ReferenceCase("01_ratio_identity", "Ratio identity",
                  "Formula identity verified within tolerance; drift quarantines; 0/0 excluded with a reason",
                  ("v3.2 §3.3 step 6", "§4.4 metric_identity_v1"), ("G09", "G05"), _build_01, _derive_01,
                  {"status": "PRESERVED", "v21": "tol 1e-4 identity check, quarantine on mismatch",
                   "v32": "same; zero denominators excluded explicitly", "corrections": []}),
    ReferenceCase("02_simpson_kitagawa", "Simpson reversal / Kitagawa",
                  "Both segments improve while the total falls; mix vs rate split; incomplete partition flagged",
                  ("v3.2 §4.4 kitagawa_v1",), ("G09",), _build_02, _derive_02,
                  {"status": "PRESERVED", "v21": "Kitagawa + top-K/OTHER conservation; not proof of no harm",
                   "v32": "same; incomplete partition reported as bounded truncation", "corrections": []}),
    ReferenceCase("03_dq_reporting_lag", "Reporting lag + replay leakage",
                  "Immature bucket is a DQ finding, not a domain drop; replay needs a KNOWN available_at",
                  ("v3.2 §2.2", "C09"), ("G06",), _build_03, _derive_03,
                  {"status": "CORRECTED", "v21": "DQ halt (kept) but replay by event_time/as_of",
                   "v32": "replay requires KNOWN available_at <= replay time", "corrections": ["C09"]}),
    ReferenceCase("04_valid_rct", "Valid randomized study, no anomaly",
                  "ITT identified for a declared estimand with no monitor; an EffectClaim, not an edge",
                  ("v3.2 §4.5", "§7.1", "F03", "F05"), ("G11", "G13"), _build_04, _derive_04,
                  {"status": "CORRECTED", "v21": "certificate becomes a CAUSALLY_IDENTIFIED edge",
                   "v32": "scoped EffectClaim; SRM computed at assignment unit", "corrections": ["F05", "D6"]}),
    ReferenceCase("05_cluster_rct_iid_se", "Cluster RCT with individual outcomes",
                  "Naive IID SE refused; analysis at the randomization unit or explicit UNSUPPORTED",
                  ("v3.2 §4.4 randomized_effect_v1", "§7.3", "F04"), ("G12",), _build_05, _derive_05,
                  {"status": "CORRECTED", "v21": "unit mismatch -> study invalid",
                   "v32": "cluster-level inference or UNSUPPORTED_CLUSTER_INFERENCE", "corrections": ["F04"]}),
    ReferenceCase("06_conditional_did", "Conditional DiD",
                  "ATT under untestable parallel trends; pre-trend is a diagnostic, not proof",
                  ("v3.2 §4.4 did_v1", "C04"), ("G11",), _build_06, _derive_06,
                  {"status": "CORRECTED", "v21": "pre-trend pass -> INTERVENTION_SUPPORTED",
                   "v32": "CAUSAL_EFFECT_CONDITIONAL with untestable assumptions listed", "corrections": ["C04"]}),
    ReferenceCase("07_unidentifiable_confounder", "Known unidentifiable confounder",
                  "Association reported, effect not identified, evidence gap emitted",
                  ("v3.2 §4.5", "C07", "I08"), ("G11",), _build_07, _derive_07,
                  {"status": "PRESERVED", "v21": "INCONCLUSIVE_ABSTAINED",
                   "v32": "UNIDENTIFIED + ASSOCIATION + evidence gaps", "corrections": []}),
    ReferenceCase("08_revised_bucket_eprocess", "Revised bucket + 50 replicas in an e-process",
                  "One contribution per bucket from the latest original revision; replicas add nothing",
                  ("v3.2 §2.2", "§5.4", "C03", "I11"), ("G07", "G08"), _build_08, _derive_08,
                  {"status": "CORRECTED", "v21": "root dedup; revision reconciliation unspecified",
                   "v32": "recompute path on revision; never double-consume", "corrections": ["C03"]}),
    ReferenceCase("09_migrated_legacy_record", "Migrated v1.x records",
                  "Byte receipts kept; no fabricated scope, IID, covariance or authority",
                  ("v3.2 §2.4",), ("G03",), _build_09, _derive_09,
                  {"status": "CORRECTED", "v21": "old certificates implicitly authoritative",
                   "v32": "REVIEW_REQUIRED until re-audited", "corrections": ["§2.4 row 9"]}),
    ReferenceCase("10_unauthorized_action", "Unauthorized action",
                  "A request saying environment_kind=emulator cannot unlock a production target",
                  ("v3.2 §6.2", "F12", "C10"), ("G17",), _build_10, _derive_10,
                  {"status": "CORRECTED", "v21": "scope read from the request string",
                   "v32": "trusted attestation + allowlist + bound approval", "corrections": ["F12", "C10"]}),
    ReferenceCase("11_noncompliance_itt_vs_exposure", "Noncompliance: ITT is not an exposure edge",
                  "ITT identified; D->Y edge not admitted from it; as-treated refused; CACE separate",
                  ("v3.2 §3.1 correction", "C01", "F05"), ("G11",), _build_11, _derive_11,
                  {"status": "CORRECTED", "v21": "unit alignment pass -> certified exposure edge",
                   "v32": "EffectClaim for Z->Y only", "corrections": ["C01", "F05"]}),
)
