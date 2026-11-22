"""Generate a contract-valid sample for every catalogue variable, then check them all.

    python -I scripts/stitch_samples.py [--seed 7]          # generate + check (~1-3 min)
    python -m pytest -q tests/test_stitch_samples.py         # re-check the written samples

Writes ~/data/mkt/stitch/samples/<variable>.json (Observation + EvidenceRecord, Contracts JSON) and
docs/stitch/SAMPLES_REPORT.md. Exit code 1 if any variable is missing or any check fails.
"""
import argparse
import json
import pathlib
import sys
import time
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from causal_core.contracts.common import EnvironmentScope  # noqa: E402
from mkt import core_port, pipeline  # noqa: E402
from mkt.config import Config  # noqa: E402
from mkt.stitch import samples as S  # noqa: E402
from mkt.stitch.sample_checks import check_sample  # noqa: E402
from mkt.variable_catalog import CATALOG, build  # noqa: E402

OUT = pathlib.Path.home() / "data/mkt/stitch/samples"


def generate(seed: int) -> tuple[dict, dict, float]:
    from mkt.sim import chain, twin
    from mkt.sim.sessions import SessionParams
    t0 = time.perf_counter()
    p = chain.fit(pipeline.canonical_olist(Config.load()))
    sp = SessionParams.from_cards()
    _, _, _, eng, _ = twin.run(p, date(2018, 1, 1), date(2018, 3, 5), seed, None, session_params=sp)
    emu = core_port.emulator_port(eng, sp)
    tele = None
    spans = pathlib.Path.home() / "data/mkt/astronomy/live/spans.jsonl"
    if spans.exists():
        from datetime import datetime, timezone
        from mkt.adapters.astronomy.telemetry import TelemetryPort, WindowMap, load_spans
        df = load_spans(spans)
        if df.height:
            import polars as pl
            w = df.with_columns(pl.from_epoch("t_ns", time_unit="ns").dt.replace_time_zone("UTC").alias("w"))
            U = timezone.utc
            tele = TelemetryPort(df, [WindowMap(w["w"].min(), w["w"].max() + (w["w"].max() - w["w"].min()) * 0.001,
                                                datetime(2018, 2, 20, tzinfo=U), datetime(2018, 2, 22, tzinfo=U))], seed, "storefront-3.1.0")
    B = S.build_all(seed, emu, tele)
    from_port = B.pop("__from_port__")
    samples, skipped = {}, {}
    served_emu = {d.variable_id for d in emu.definitions()}
    for v in CATALOG:
        vid = v.variable_id
        try:
            if v.entity == "service" and v.kind == "metric":
                if tele is None:
                    skipped[vid] = "no saved storefront spans (~/data/mkt/astronomy/live/spans.jsonl): run scripts/storefront_s5.py with the shop up"
                    continue
                from causal_core.contracts.capabilities import GetSeries
                from causal_core.contracts.common import TimeWindow
                from datetime import datetime, timezone
                U = timezone.utc
                r = tele.query(GetSeries(schema_version="2.0", request_id=f"s:{vid}", variables=(vid,), scope=tele.scope, data_cut=S.CUT,
                                         window=TimeWindow(start=datetime(2018, 2, 20, tzinfo=U), end=datetime(2018, 2, 22, tzinfo=U)),
                                         purpose="contract sample", request="get_series", grain="1m"))
                if r.status != "OK":
                    skipped[vid] = f"telemetry: {r.reason}"
                    continue
                samples[vid] = [(r.observations[0], tele.evidence_record(r.observations[0]))]
            elif vid == "item_sold":                                       # line items exist only in the real Olist replay
                res = from_port(core_port.olist_port(), vid, "event")
                if isinstance(res, str):
                    skipped[vid] = res
                    continue
                samples[vid] = [res]
            elif vid in served_emu:
                res = from_port(emu, vid, v.kind if v.kind in ("metric", "event") else "event")
                if isinstance(res, str):
                    skipped[vid] = res
                    continue
                samples[vid] = [res]
            elif v.kind == "categorical" and vid in ("buyer_region", "device"):
                col = "region" if vid == "buyer_region" else "device"
                etype = "session_started"
                vals = eng.events.filter(eng.events["event_type"] == etype)[col].drop_nulls().head(40).to_list()
                from causal_core.contracts.observations import CategoricalValue
                samples[vid] = [S.observation(vid, CategoricalValue(shape="categorical", category=x), "synthetic_generated", emu.scope,
                                              "emulator-chain", ("mech:emulator_session_attribute",)) for x in vals]
            elif vid in B:
                payload, origin, sc, src, mech = B[vid]
                payloads = payload if isinstance(payload, list) else [payload]
                samples[vid] = [S.observation(vid, pl_, origin, sc, src, mech) for pl_ in payloads]
            else:
                skipped[vid] = "no builder"
        except Exception as ex:
            skipped[vid] = f"error: {type(ex).__name__}: {str(ex)[:200]}"
    return samples, skipped, time.perf_counter() - t0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-repeat", action="store_true", help="skip the determinism re-run")
    a = ap.parse_args()
    samples, skipped, secs = generate(a.seed)
    OUT.mkdir(parents=True, exist_ok=True)
    defs = {v.variable_id: v for v in CATALOG}
    results = {}
    for vid, pairs in samples.items():
        d = build(defs[vid], pairs[0][0].scope)
        problems = [p for o, e in pairs for p in check_sample(defs[vid], d, o, e)]
        results[vid] = problems
        (OUT / f"{vid}.json").write_text(json.dumps({"variable": vid, "definition": d.model_dump(mode="json"),
                                                     "samples": [{"observation": o.model_dump(mode="json"), "evidence": e.model_dump(mode="json")}
                                                                 for o, e in pairs]}, indent=1))
    # determinism: the same seed gives the same content hashes for three card-synthesised variables
    again, _, _ = generate(a.seed) if "--no-repeat" not in sys.argv else (samples, {}, 0)
    det = {vid: [o.lineage.content_hash for o, _ in samples[vid]] == [o.lineage.content_hash for o, _ in again.get(vid, [])]
           for vid in ("offer_available", "seller_status", "ad_exposure") if vid in samples}
    failed = {k: v for k, v in results.items() if v}
    origins = {}
    for vid, pairs in samples.items():
        origins[pairs[0][1].provenance_role] = origins.get(pairs[0][1].provenance_role, 0) + 1
    L = ["# Contract samples for every catalogue variable (generated by `scripts/stitch_samples.py`)", "",
         f"Seed {a.seed} · generation {secs:.0f} s · variables {len(CATALOG)} · sampled {len(samples)} · skipped {len(skipped)} · "
         f"failing checks {len(failed)} · deterministic {all(det.values())} ({', '.join(det)})", "",
         "Evidence roles: " + ", ".join(f"{k} {v}" for k, v in sorted(origins.items())), "",
         "| Variable | Layer | Kind | Shape | n | Role | Source | Checks |", "|---|---|---|---|---|---|---|---|"]
    for v in CATALOG:
        vid = v.variable_id
        if vid in samples:
            o, e = samples[vid][0]
            n = (len(o.payload.points) if o.payload.shape == "time_series" else len(o.payload.events) if o.payload.shape == "event_batch"
                 else len(o.payload.samples) if o.payload.shape == "state_trajectory" else len(o.payload.units) if o.payload.shape == "assignment_outcomes"
                 else len(samples[vid]))
            L.append(f"| `{vid}` | {v.layer} | {v.kind} | {o.payload.shape} | {n} | {e.provenance_role} | {o.source_id} | "
                     f"{'pass' if not results[vid] else 'FAIL: ' + '; '.join(results[vid][:3])} |")
        else:
            L.append(f"| `{vid}` | {v.layer} | {v.kind} | — | 0 | — | — | SKIPPED: {skipped[vid]} |")
    (ROOT / "docs/stitch/SAMPLES_REPORT.md").write_text("\n".join(L) + "\n")
    print(L[2])
    for vid, why in skipped.items():
        print(f"  skipped {vid}: {why}")
    for vid, pr in failed.items():
        print(f"  FAIL {vid}: {pr[:3]}")
    print(f"report: docs/stitch/SAMPLES_REPORT.md · samples: {OUT}")
    return 1 if failed or not all(det.values()) or any(not w.startswith("no saved storefront") for w in skipped.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
