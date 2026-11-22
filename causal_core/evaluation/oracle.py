"""Independent reference calculations for the Phase 0 fixtures.

This module is the yardstick that Phase 4's production kernels get diffed
against, so it shares nothing with them: stdlib only, written for
readability, no numpy/scipy. If a production estimator and this file disagree
on a frozen fixture, one of them is wrong and a human decides which one
(plan §A.1: "independent checks must not reuse the production inference
implementation as its only oracle").

Every function is pure: inputs in, plain dicts out.
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime
from statistics import NormalDist

_STD_NORMAL = NormalDist()

# ── distributions ──────────────────────────────────────────────────────────


def normal_two_sided_p(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2.0))


def normal_critical(level: float = 0.95) -> float:
    return _STD_NORMAL.inv_cdf(0.5 + level / 2.0)


def chi2_1df_sf(x: float) -> float:
    """P(X >= x) for chi-square with one degree of freedom."""
    return math.erfc(math.sqrt(x / 2.0))


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta (modified Lentz)."""
    tiny, eps = 1e-300, 3e-15
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        for aa in (m * (b - m) * x / ((qam + m2) * (a + m2)),
                   -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))):
            d = 1.0 + aa * d
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = 1.0 + aa / c
            c = c if abs(c) > tiny else tiny
            h *= d * c
        if abs(d * c - 1.0) < eps:
            return h
    raise ArithmeticError("incomplete beta did not converge")


def regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_front = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                 + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(log_front) * _betacf(a, b, x) / a
    return 1.0 - math.exp(log_front) * _betacf(b, a, 1.0 - x) / b


def student_t_two_sided_p(t: float, df: float) -> float:
    return regularized_incomplete_beta(df / 2.0, 0.5, df / (df + t * t))


def student_t_critical(df: float, level: float = 0.95) -> float:
    lo, hi = 0.0, 1000.0
    for _ in range(200):  # bisection: p(t) is monotone decreasing in |t|
        mid = 0.5 * (lo + hi)
        if student_t_two_sided_p(mid, df) > 1.0 - level:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ── small sample helpers ───────────────────────────────────────────────────


def mean(xs) -> float:
    xs = list(xs)
    return math.fsum(xs) / len(xs)


def sample_var(xs) -> float:
    xs = list(xs)
    m = mean(xs)
    return math.fsum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def ols(xs, ys) -> dict:
    """Simple regression y = a + b x with the classical slope SE."""
    xs, ys = list(xs), list(ys)
    n, mx, my = len(xs), mean(xs), mean(ys)
    sxx = math.fsum((x - mx) ** 2 for x in xs)
    sxy = math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx
    rss = math.fsum((y - intercept - slope * x) ** 2 for x, y in zip(xs, ys))
    return {"slope": slope, "intercept": intercept, "n": n,
            "se_slope": math.sqrt(rss / (n - 2) / sxx) if n > 2 else None}


def two_sample_pooled_t(a, b, level: float = 0.95) -> dict:
    """Difference mean(a) - mean(b), pooled-variance t, df = n_a + n_b - 2."""
    a, b = list(a), list(b)
    na, nb = len(a), len(b)
    sp2 = ((na - 1) * sample_var(a) + (nb - 1) * sample_var(b)) / (na + nb - 2)
    est = mean(a) - mean(b)
    se = math.sqrt(sp2 * (1.0 / na + 1.0 / nb))
    df = na + nb - 2
    t = est / se
    half = student_t_critical(df, level) * se
    return {"estimate": est, "se": se, "df": df, "t": t,
            "p_value": student_t_two_sided_p(t, df), "ci": [est - half, est + half]}


# ── 01 metric identity ─────────────────────────────────────────────────────


def check_ratio_identity(rows: list[dict], tol: float) -> dict:
    """reported ≈ numerator / denominator, row by row.

    Zero denominators are excluded *with a reason* (v2.1 left this
    unspecified); they are neither a residual of 0 nor a failure.
    """
    per_row, residuals = [], []
    for r in rows:
        if r["denominator"] == 0:
            per_row.append({"bucket": r["bucket"], "status": "ZERO_DENOMINATOR", "residual": None})
            continue
        if r["reported"] is None:
            per_row.append({"bucket": r["bucket"], "status": "REPORTED_MISSING", "residual": None})
            continue
        res = r["reported"] - r["numerator"] / r["denominator"]
        residuals.append(abs(res))
        per_row.append({"bucket": r["bucket"], "status": "OK" if abs(res) <= tol else "MISMATCH",
                        "residual": res})
    if not residuals:
        verdict = "UNVERIFIED"
    else:
        verdict = "VERIFIED_IDENTITY" if max(residuals) <= tol else "IDENTITY_MISMATCH"
    return {"verdict": verdict,
            "edge_status": "ACTIVE" if verdict == "VERIFIED_IDENTITY" else "QUARANTINED",
            "max_abs_residual": max(residuals) if residuals else None,
            "rows_checked": len(residuals), "rows": per_row}


# ── 02 Kitagawa mix-vs-rate ────────────────────────────────────────────────


def kitagawa(segments: list[dict], declared_totals: dict | None = None) -> dict:
    """ΔR = Σ w̄ (r1 − r0)  [rate]  +  Σ r̄ (w1 − w0)  [mix]   (exact identity).

    ``declared_totals`` = {"den0": .., "den1": ..} for the full population. If
    the listed segments do not cover it, the decomposition only explains the
    covered part and says so instead of pretending to explain the aggregate.
    """
    if any(s["den0"] == 0 or s["den1"] == 0 for s in segments):
        return {"verdict": "REFUSED", "refusal": "ZERO_DENOMINATOR_SEGMENT"}
    d0 = math.fsum(s["den0"] for s in segments)
    d1 = math.fsum(s["den1"] for s in segments)
    r0_total = math.fsum(s["num0"] for s in segments) / d0
    r1_total = math.fsum(s["num1"] for s in segments) / d1
    parts, rate, mix = [], [], []
    for s in segments:
        w0, w1 = s["den0"] / d0, s["den1"] / d1
        r0, r1 = s["num0"] / s["den0"], s["num1"] / s["den1"]
        rate_g, mix_g = (w0 + w1) / 2 * (r1 - r0), (r0 + r1) / 2 * (w1 - w0)
        rate.append(rate_g)
        mix.append(mix_g)
        parts.append({"segment": s["segment"], "rate_0": r0, "rate_1": r1,
                      "weight_0": w0, "weight_1": w1,
                      "rate_contribution": rate_g, "mix_contribution": mix_g})
    out = {
        "verdict": "DECOMPOSED", "claim_kind": "MATHEMATICAL_IDENTITY",
        "aggregate_rate_0": r0_total, "aggregate_rate_1": r1_total,
        "total_change": r1_total - r0_total,
        "rate_component": math.fsum(rate), "mix_component": math.fsum(mix),
        "identity_residual": (r1_total - r0_total) - math.fsum(rate) - math.fsum(mix),
        "segments": parts,
        "every_segment_rate_rose": all(p["rate_1"] > p["rate_0"] for p in parts),
        "partition": "COMPLETE",
    }
    if declared_totals:
        cov0, cov1 = d0 / declared_totals["den0"], d1 / declared_totals["den1"]
        if cov0 < 1.0 or cov1 < 1.0:
            out.update(partition="INCOMPLETE_PARTITION", verdict="DECOMPOSED_COVERED_SUBSET_ONLY",
                       coverage_0=cov0, coverage_1=cov1,
                       uncovered_share_max=1.0 - min(cov0, cov1))
    return out


# ── 03 data quality and replay availability ────────────────────────────────


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def dq_maturity(buckets: list[dict], threshold: float) -> dict:
    mature = [b for b in buckets if b["completeness"] >= threshold]
    immature = [b for b in buckets if b["completeness"] < threshold]
    baseline = mean(b["value"] for b in mature) if mature else None
    return {
        "threshold": threshold,
        "mature_buckets": [b["bucket"] for b in mature],
        "immature_buckets": [{"bucket": b["bucket"], "completeness": b["completeness"],
                              "naive_change_vs_mature_mean": b["value"] / baseline - 1.0}
                             for b in immature],
        "verdict": "DQ_IMMATURE_WINDOW" if immature else "DQ_OK",
    }


def replay_visibility(observations: list[dict], replay_time: str) -> dict:
    """Leakage-safe visibility: a KNOWN available_at <= replay_time is required.

    Returns the v3.2 split and, for contrast, what an observed_at-only rule
    (v2.1's event_time/as_of replay) would have shown.
    """
    t = _ts(replay_time)
    visible, hidden, refused, naive = [], [], [], []
    for o in observations:
        if _ts(o["observed_at"]) <= t:
            naive.append(o["id"])
        quality = o["available_at_quality"]
        if quality != "KNOWN":
            refused.append({"id": o["id"], "reason": f"{quality}_AVAILABILITY_CLOCK"})
        elif _ts(o["available_at"]) <= t:
            visible.append(o["id"])
        else:
            hidden.append({"id": o["id"], "reason": "NOT_YET_AVAILABLE"})
    hidden_ids = {h["id"] for h in hidden}
    refused_ids = {r["id"] for r in refused}
    return {"visible": visible, "hidden": hidden, "refused": refused,
            "observed_at_only_rule_would_show": naive,
            # known to arrive after the replay time: a genuine look-ahead leak
            "leak_under_observed_at_rule": [i for i in naive if i in hidden_ids],
            # availability never attested: cannot prove it was visible then
            "unverifiable_under_observed_at_rule": [i for i in naive if i in refused_ids]}


# ── 04 randomized study ────────────────────────────────────────────────────


def srm_two_arm(counts: dict, expected_share: dict) -> dict:
    if len(counts) != 2:
        raise ValueError("Phase 0 oracle supports two-arm SRM only")
    n = sum(counts.values())
    chi2 = math.fsum((counts[k] - n * expected_share[k]) ** 2 / (n * expected_share[k])
                     for k in counts)
    return {"chi2": chi2, "df": 1, "p_value": chi2_1df_sf(chi2)}


def diff_in_proportions(x1: int, n1: int, x0: int, n0: int, level: float = 0.95) -> dict:
    p1, p0 = x1 / n1, x0 / n0
    est = p1 - p0
    se = math.sqrt(p1 * (1 - p1) / n1 + p0 * (1 - p0) / n0)
    half = normal_critical(level) * se
    return {"p_treatment": p1, "p_control": p0, "estimate": est, "se": se,
            "z": est / se, "p_value": normal_two_sided_p(est / se), "ci": [est - half, est + half]}


# ── 05 cluster-randomized inference ────────────────────────────────────────


def naive_iid_difference(y_treat, y_control, level: float = 0.95) -> dict:
    """Individuals treated as independent — *invalid* when clusters were randomized."""
    y_treat, y_control = list(y_treat), list(y_control)
    est = mean(y_treat) - mean(y_control)
    se = math.sqrt(sample_var(y_treat) / len(y_treat) + sample_var(y_control) / len(y_control))
    half = normal_critical(level) * se
    return {"estimate": est, "se": se, "p_value": normal_two_sided_p(est / se),
            "ci": [est - half, est + half], "n_units": len(y_treat) + len(y_control)}


def cluster_inference_support(clusters_per_arm: dict[str, int], equal_cluster_sizes: bool) -> tuple[str, str]:
    """Decide whether cluster-level inference is trustworthy enough to report.

    Returns (status, reason) where status is one of
    "SUPPORTED", "SUPPORTED_WITH_LIMITATIONS", "UNSUPPORTED".
    "UNSUPPORTED" maps to the blueprint's UNSUPPORTED_CLUSTER_INFERENCE refusal.
    """
    # Thresholds follow standard cluster-trial guidance (Hayes & Moulton): a
    # t-test on cluster means with df = G - 2 is the recommended analysis when
    # there are fewer than ~15 clusters per arm, valid under roughly normal
    # cluster means and equal sizes. Sandwich SEs are not used at this scale.
    smallest = min(clusters_per_arm.values())
    if smallest < 4:
        return ("UNSUPPORTED",
                f"only {smallest} randomized clusters in the smallest arm; "
                "no credible between-cluster variance estimate")
    limits = []
    if smallest < 15:
        limits.append(f"{smallest} clusters in the smallest arm: small-sample t on cluster means "
                      "(df = G - 2), assumes approximately normal cluster means")
    if not equal_cluster_sizes:
        limits.append("unequal cluster sizes: unweighted cluster means change the estimand "
                      "and weaken the t approximation")
    if limits:
        return "SUPPORTED_WITH_LIMITATIONS", "; ".join(limits)
    return "SUPPORTED", f"{smallest}+ equal-sized clusters per arm"


def cluster_level_analysis(rows: list[dict], level: float = 0.95) -> dict:
    """Analyse at the randomization unit: one mean per cluster, t-test on cluster means."""
    by_cluster: dict[str, list[float]] = {}
    arm_of: dict[str, str] = {}
    for r in rows:
        by_cluster.setdefault(r["cluster"], []).append(r["y"])
        arm_of[r["cluster"]] = r["arm"]
    means = {c: mean(v) for c, v in by_cluster.items()}
    sizes = {len(v) for v in by_cluster.values()}
    per_arm = {arm: sum(1 for c in means if arm_of[c] == arm) for arm in ("treatment", "control")}
    status, reason = cluster_inference_support(per_arm, equal_cluster_sizes=len(sizes) == 1)
    out = {"clusters_per_arm": per_arm, "equal_cluster_sizes": len(sizes) == 1,
           "support": status, "support_reason": reason}
    if status != "UNSUPPORTED":
        out.update(two_sample_pooled_t([m for c, m in means.items() if arm_of[c] == "treatment"],
                                       [m for c, m in means.items() if arm_of[c] == "control"], level))
    return out


# ── 06 difference-in-differences ───────────────────────────────────────────


def did_collapsed(panel: list[dict], level: float = 0.95) -> dict:
    """Collapse each unit to (post mean − pre mean), then compare groups.

    Collapsing removes serial correlation from the SE (Bertrand, Duflo &
    Mullainathan 2004). The pre-trend slope comparison is a *diagnostic*: a
    pass is consistent with parallel trends, never proof of it.
    """
    units: dict[str, dict] = {}
    for r in panel:
        u = units.setdefault(r["unit"], {"group": r["group"], "pre": [], "post": [], "pre_t": []})
        if r["period"] < 0:
            u["pre"].append(r["y"])
            u["pre_t"].append(r["period"])
        else:
            u["post"].append(r["y"])
    change = {g: [mean(u["post"]) - mean(u["pre"]) for u in units.values() if u["group"] == g]
              for g in ("treated", "control")}
    slope = {g: [ols(u["pre_t"], u["pre"])["slope"] for u in units.values() if u["group"] == g]
             for g in ("treated", "control")}
    effect = two_sample_pooled_t(change["treated"], change["control"], level)
    pre = two_sample_pooled_t(slope["treated"], slope["control"], level)
    return {"att": effect, "pretrend_slope_difference": pre,
            "pretrend_diagnostic": ("NO_DETECTABLE_DIVERGENCE" if pre["p_value"] >= 1 - level
                                    else "DIVERGENCE_DETECTED"),
            "units_per_group": {g: len(v) for g, v in change.items()}}


# ── 07 identification audit (latent confounding) ───────────────────────────


def audit_latent_backdoor(edges: list[dict], treatment: str, outcome: str, design: str,
                          adjustment_set: list[str], instruments: list[str]) -> dict:
    """Minimal audit: is there a declared latent common cause of T and Y that no
    design element blocks? Only the *relevant* pair is checked (C07)."""
    latent_pair = any(e["kind"] == "LATENT_CONFOUNDING"
                      and {e["source"], e["target"]} == {treatment, outcome} for e in edges)
    if design == "randomized":
        return {"status": "CONDITIONAL_IDENTIFICATION", "blocking": []}
    if latent_pair and not instruments:
        return {"status": "UNIDENTIFIED", "claim_kind": "ASSOCIATION",
                "blocking": [f"LATENT_CONFOUNDING({treatment},{outcome}) unblocked; "
                             f"observed adjustment set {adjustment_set or '[]'} cannot block a latent path"]}
    return {"status": "CONDITIONAL_IDENTIFICATION", "blocking": [],
            "untestable": ["no unmeasured confounding given adjustment set"]}


# ── 08 sequential e-process with revisions and replicas ────────────────────


def eprocess_log_wealth(records: list[dict], lam: float, alpha: float) -> dict:
    """Fixed-bet Gaussian test martingale: log E += λz − λ²/2 per bucket.

    Valid under H0: z_t i.i.d. N(0,1) (Ville's inequality bounds the
    false-alarm probability by α). One contribution per bucket: the highest
    revision of an *original* record. Synthetic replicas add nothing; a
    revision replaces, never adds.
    """
    chosen: dict[str, dict] = {}
    ignored = []
    for r in records:
        if r["origin_kind"] != "original":
            ignored.append({"id": r["id"], "reason": "SYNTHETIC_NO_INDEPENDENT_SUPPORT"})
            continue
        cur = chosen.get(r["bucket"])
        if cur is None or r["revision"] > cur["revision"]:
            if cur is not None:
                ignored.append({"id": cur["id"], "reason": "SUPERSEDED_REVISION"})
            chosen[r["bucket"]] = r
        elif r["revision"] == cur["revision"] and r["z"] != cur["z"]:
            raise ValueError(f"conflicting equal revision for bucket {r['bucket']}: quarantine")
        else:
            ignored.append({"id": r["id"], "reason": "SUPERSEDED_REVISION"})
    path, total, peak = [], 0.0, 0.0
    for bucket in sorted(chosen):
        total += lam * chosen[bucket]["z"] - lam * lam / 2.0
        peak = max(peak, total)
        path.append({"bucket": bucket, "used": chosen[bucket]["id"], "log_wealth": total})
    threshold = math.log(1.0 / alpha)
    return {"log_wealth": total, "peak_log_wealth": peak, "threshold": threshold,
            "alarm": peak >= threshold, "path": path, "ignored": ignored}


def eprocess_first_seen_root(records: list[dict], lam: float, alpha: float) -> dict:
    """A literal 'consume each lineage root once' rule: the first record seen per
    root wins, so a later *correction* sharing that root is dropped."""
    seen, total, peak = set(), 0.0, 0.0
    for r in records:
        if r["origin_kind"] != "original" or r["lineage_root"] in seen:
            continue
        seen.add(r["lineage_root"])
        total += lam * r["z"] - lam * lam / 2.0
        peak = max(peak, total)
    return {"log_wealth": total, "peak_log_wealth": peak, "alarm": peak >= math.log(1.0 / alpha)}


def eprocess_unguarded(records: list[dict], lam: float, alpha: float) -> dict:
    """Every record counted: replicas and both revisions. The failure mode."""
    total = math.fsum(lam * r["z"] - lam * lam / 2.0 for r in records)
    return {"log_wealth": total, "alarm": total >= math.log(1.0 / alpha)}


# ── 09 legacy migration ────────────────────────────────────────────────────


def sha256_hex(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def legacy_lineage_root(raw: str) -> str:
    """Deterministic, content-derived root for records with no attested origin."""
    return "legacy:" + sha256_hex(raw)[:16]


# ── 10 action policy ───────────────────────────────────────────────────────


def policy_v32(proposal: dict, sandbox_allowlist: list[str], evaluated_at: str) -> dict:
    """Deny-by-default. The request's own ``environment_kind`` string is ignored:
    only a trusted attestation for an allowlisted target counts (C10, F12)."""
    reasons = []
    if proposal.get("environment_attestation") is None:
        reasons.append("UNTRUSTED_ENVIRONMENT_CLAIM")
    if proposal["target"] not in sandbox_allowlist:
        reasons.append("TARGET_NOT_IN_SANDBOX_ALLOWLIST")
    approval = proposal.get("approval")
    if approval is None:
        reasons.append("APPROVAL_MISSING")
    else:
        if _ts(approval["expires_at"]) <= _ts(evaluated_at):
            reasons.append("APPROVAL_EXPIRED")
        if approval["bound_target"] != proposal["target"]:
            reasons.append("APPROVAL_NOT_BOUND_TO_TARGET")
    if proposal.get("action_kind") != "sandbox_test":
        reasons.append("ONLY_SANDBOX_TESTS_ARE_EXECUTABLE")
    return {"decision": "DENY" if reasons else "ALLOW_SANDBOX_TEST_ONLY", "reasons": reasons,
            "production_write_path_exists": False}


def policy_v21_literal(proposal: dict, evaluated_at: str) -> dict:
    """v2.1 §5.6 as written: emulator scope + approval + expiry, where 'emulator
    scope' is read from the request's environment_kind field."""
    approval = proposal.get("approval")
    ok = (proposal.get("environment_kind") == "emulator" and approval is not None
          and _ts(approval["expires_at"]) > _ts(evaluated_at))
    return {"decision": "ALLOW" if ok else "DENY"}


# ── 11 noncompliance: ITT vs exposure ──────────────────────────────────────


def _cov(xs, ys) -> float:
    mx, my = mean(xs), mean(ys)
    return math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (len(xs) - 1)


def noncompliance_analysis(rows: list[dict], level: float = 0.95) -> dict:
    z1 = [r for r in rows if r["z"] == 1]
    z0 = [r for r in rows if r["z"] == 0]
    itt = naive_iid_difference([r["y"] for r in z1], [r["y"] for r in z0], level)  # valid: units randomized
    first = naive_iid_difference([r["d"] for r in z1], [r["d"] for r in z0], level)
    a, b = itt["estimate"], first["estimate"]
    cov_ab = (_cov([r["y"] for r in z1], [r["d"] for r in z1]) / len(z1)
              + _cov([r["y"] for r in z0], [r["d"] for r in z0]) / len(z0))
    wald_var = (itt["se"] ** 2 / b ** 2 + a ** 2 * first["se"] ** 2 / b ** 4
                - 2 * a * cov_ab / b ** 3)
    wald_se = math.sqrt(wald_var)
    half = normal_critical(level) * wald_se
    exposed = [r["y"] for r in rows if r["d"] == 1]
    unexposed = [r["y"] for r in rows if r["d"] == 0]
    return {
        "itt": itt,
        "first_stage_takeup": first,
        "wald_iv_cace": {"estimate": a / b, "se": wald_se, "ci": [a / b - half, a / b + half],
                         "requires": ["exclusion restriction", "monotonicity (no defiers)",
                                      "first stage ≠ 0"]},
        "as_treated_comparison": {"estimate": mean(exposed) - mean(unexposed),
                                  "status": "REFUSED_AS_CAUSAL",
                                  "reason": "selection into exposure"},
    }
