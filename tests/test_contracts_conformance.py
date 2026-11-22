"""Conformance to Contracts/ (schema 2.0): gates S8 (core compatibility), S6 (provenance roles / firewall),
S3 (no availability past the data cut) re-run on the contract port, plus import boundary and I15."""
import ast
import pathlib
from datetime import date, datetime, timezone

import pytest

from causal_core.contracts.capabilities import CapabilityResponse, GetAggregate, GetAssignmentOutcomes, GetEventBatch, GetSeries
from causal_core.contracts.common import TimeWindow
from causal_core.contracts.evidence import EvidenceRecord
from causal_core.contracts.ports import EvidenceAccessPort
from mkt import core_port, pipeline, registry
from mkt.sim import chain, twin

U = timezone.utc
CUT = datetime(2018, 5, 1, tzinfo=U)
WIN = TimeWindow(start=datetime(2018, 1, 1, 3, tzinfo=U), end=datetime(2018, 3, 5, 3, tzinfo=U))
ROOT = pathlib.Path(__file__).resolve().parents[1]


def _req(cls, port, **kw):
    base = dict(schema_version="2.0", request_id="r1", scope=port.scope, data_cut=CUT, purpose="conformance test")
    return cls(**{**base, **kw})


@pytest.fixture(scope="module")
def olist(cfg):
    try:
        registry.load("olist", cfg.artifacts)
    except registry.MissingSource:
        pytest.skip("Olist not registered")
    return core_port.olist_port(cfg)


@pytest.fixture(scope="module")
def emu(cfg):
    try:
        registry.load("olist", cfg.artifacts)
    except registry.MissingSource:
        pytest.skip("Olist not registered")
    p = chain.fit(pipeline.canonical_olist(cfg))
    _, _, _, eng, _ = twin.run(p, date(2018, 1, 1), date(2018, 3, 5), 81, None)
    return core_port.emulator_port(eng, hidden_variables={"payments_count", "payment_approved"})


def _roundtrip(r: CapabilityResponse):
    """Strict re-validation from JSON: the response is a genuine Contracts object, not a look-alike."""
    assert CapabilityResponse.model_validate_json(r.model_dump_json()) == r


@pytest.mark.parametrize("which", ["olist", "emu"])
def test_s8_every_request_kind_validates(which, request):
    port = request.getfixturevalue(which)
    assert isinstance(port, EvidenceAccessPort)
    m = port.manifest()
    assert set(m.supported_requests) >= {"get_series", "get_aggregate", "get_event_batch"}
    for r in (port.query(_req(GetSeries, port, variables=("orders_count",), window=WIN, request="get_series", grain="1w")),
              port.query(_req(GetAggregate, port, variables=("orders_count", "mean_review_score"), window=WIN, request="get_aggregate")),
              port.query(_req(GetEventBatch, port, variables=("order_placed",), request="get_event_batch",
                              window=TimeWindow(start=WIN.start, end=datetime(2018, 1, 2, 3, tzinfo=U))))):
        assert r.status == "OK", r.reason
        _roundtrip(r)
    for d in port.definitions():
        type(d).model_validate_json(d.model_dump_json())


@pytest.mark.parametrize("which", ["olist", "emu"])
def test_s8_unsupported_and_budget_are_explained(which, request):
    port = request.getfixturevalue(which)
    r = port.query(_req(GetAssignmentOutcomes, port, variables=("orders_count",), request="get_assignment_outcomes", study_id="none"))
    assert r.status == "UNSUPPORTED" and r.reason and not r.observations
    r = port.query(_req(GetSeries, port, variables=("no_such_variable",), request="get_series", grain="1w"))
    assert r.status == "UNSUPPORTED" and "no_such_variable" in r.reason
    r = port.query(_req(GetEventBatch, port, variables=("order_placed",), request="get_event_batch", max_rows=10))
    assert r.status == "BUDGET_EXCEEDED" and r.reason
    r = port.query(_req(GetSeries, port, variables=("orders_count",), request="get_series", grain="1w", window=WIN, max_buckets=3))
    assert r.status == "BUDGET_EXCEEDED"
    _roundtrip(r)


def test_s6_roles_scopes_and_origins(olist, emu):
    o = olist.query(_req(GetSeries, olist, variables=("orders_count",), window=WIN, request="get_series", grain="1w")).observations[0]
    e = emu.query(_req(GetSeries, emu, variables=("orders_count",), window=WIN, request="get_series", grain="1w")).observations[0]
    # G2 (owner decision 22 Nov): replayed real history is offline_replay, hence SANDBOX_OBSERVED, never REAL_OBSERVED
    assert o.lineage.origin_kind == "original" and o.scope.environment_kind == "offline_replay"
    assert olist.evidence_record(o).provenance_role == "SANDBOX_OBSERVED"
    with pytest.raises(ValueError):
        EvidenceRecord(evidence_id="x", lineage=o.lineage, scope=o.scope, provenance_role="REAL_OBSERVED", content_hash=o.lineage.content_hash)
    assert e.lineage.origin_kind == "synthetic_generated" and e.lineage.generator_version and e.scope.environment_kind == "emulator"
    assert emu.evidence_record(e).provenance_role == "SYNTHETIC_GENERATED"
    with pytest.raises(ValueError):                       # the contract itself forbids relabelling emulator output as real
        EvidenceRecord(evidence_id="x", lineage=e.lineage, scope=e.scope, provenance_role="REAL_OBSERVED", content_hash=e.lineage.content_hash)
    assert o.clocks.available_at.quality == "ESTIMATED" and e.clocks.available_at.quality == "KNOWN"
    dumped = e.model_dump_json()
    for leak in ("twin", "truth", "oracle", "latent", "causes"):
        assert leak not in dumped.lower()


def test_s6_hidden_variables_until_exposed(emu):
    r = emu.query(_req(GetSeries, emu, variables=("payments_count",), window=WIN, request="get_series", grain="1w"))
    assert r.status == "UNSUPPORTED"
    assert "payments_count" not in {d.variable_id for d in emu.definitions()}
    assert "order_payment_ratio" not in {d.variable_id for d in emu.definitions()}


def test_s3_nothing_available_after_the_data_cut(olist, emu):
    for port in (olist, emu):
        r = port.query(_req(GetSeries, port, variables=("orders_count",), window=WIN, request="get_series", grain="1w"))
        o = r.observations[0]
        assert o.clocks.available_at.value <= CUT and all(p.t < CUT for p in o.payload.points)


def test_identities_are_formula_asts(olist):
    d = {x.variable_id: x for x in olist.definitions()}["order_payment_ratio"]
    assert d.aggregation_kind == "ratio" and d.formula_ast.op == "div" and d.identity_tolerance


def test_i15_ports_are_read_only(olist, emu):
    for port in (olist, emu):
        assert not [m for m in dir(port) if m.split("_")[0] in ("act", "intervene", "write", "propose", "execute", "actuate")]


def test_import_boundary_core_side():
    banned = ("mkt.sim", "mkt.emulator", "mkt.adapters", "mkt.privileged", "mkt.replay_port", "mkt.stitch", "polars", "duckdb")
    for f in [ROOT / "mkt/standin_core.py", *(ROOT / "Contracts").glob("*.py")]:
        tree = ast.parse(f.read_text())
        mods = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module] + \
               [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        bad = [m for m in mods if m.startswith(banned) or (m.startswith("mkt") and f.name != "standin_core.py")]
        assert not bad, f"{f.name} imports {bad}"


# ---------------------------------------------------------------- variable taxonomy (G1)
from causal_core.contracts.observations import ALLOWED_SHAPES  # noqa: E402
from causal_core.contracts.variables import METRIC_ONLY_FIELDS  # noqa: E402


@pytest.fixture(scope="module")
def emu_sessions(cfg):
    from mkt.sim.sessions import SessionParams
    try:
        registry.load("olist", cfg.artifacts)
        sp = SessionParams.from_cards()
    except (registry.MissingSource, FileNotFoundError):
        pytest.skip("Olist or transport cards missing")
    p = chain.fit(pipeline.canonical_olist(cfg))
    _, _, _, eng, _ = twin.run(p, date(2018, 1, 1), date(2018, 1, 22), 82, None, session_params=sp)
    return core_port.emulator_port(eng, sp)


@pytest.mark.parametrize("which", ["olist", "emu_sessions"])
def test_taxonomy_definitions_are_complete_and_related(which, request):
    port = request.getfixturevalue(which)
    defs = {d.variable_id: d for d in port.definitions()}
    for d in defs.values():
        assert d.entity_ref and d.description, d.variable_id
        if d.kind != "metric":
            assert not any(f in type(d).model_fields for f in METRIC_ONLY_FIELDS)
            continue
        assert d.aggregation_kind and d.population_coverage and d.time_semantics
        if d.formula_ast is not None:                                    # relations are declared, and resolve
            refs = {d.formula_ast.left.variable_id, d.formula_ast.right.variable_id}
            assert refs <= set(defs), (d.variable_id, refs - set(defs))
            assert d.aggregation_kind == "ratio"
            assert all(defs[r].entity_ref == d.entity_ref or d.variable_id == "completion_rate" for r in refs)
        if d.identity_tolerance is not None:
            assert d.formula_ast is not None
    if which == "emu_sessions":
        assert {"cart_rate", "checkout_rate", "completion_rate", "views_per_session", "late_share"} <= set(defs)


@pytest.mark.parametrize("which", ["olist", "emu_sessions"])
def test_taxonomy_served_shapes_and_event_fields(which, request):
    port = request.getfixturevalue(which)
    win = TimeWindow(start=datetime(2018, 1, 8, 3, tzinfo=U), end=datetime(2018, 1, 9, 3, tzinfo=U))
    for d in port.definitions():
        if d.kind == "event":
            r = port.query(_req(GetEventBatch, port, variables=(d.variable_id,), request="get_event_batch", window=win, max_rows=200_000,
                                max_events=200_000, data_cut=datetime(2018, 1, 22, tzinfo=U)))
            if r.status != "OK":
                assert "no events" in r.reason, (d.variable_id, r.reason)
                continue
            o = r.observations[0]
            assert o.payload.shape in ALLOWED_SHAPES["event"]
            ev = o.payload.events[0]
            assert set(ev.attributes) == set(d.value_schema.fields), d.variable_id
            ident = d.value_schema.identity_fields[0]
            assert all(e.attributes[ident] is not None for e in o.payload.events)
        else:
            r = port.query(_req(GetSeries, port, variables=(d.variable_id,), request="get_series", grain="1w", window=WIN))
            assert r.status == "OK", (d.variable_id, r.reason)
            assert r.observations[0].payload.shape in ALLOWED_SHAPES["metric"]
            vals = [p.value for p in r.observations[0].payload.points if p.value is not None]
            if d.value_schema.dtype == "proportion":
                assert all(0 <= v <= 1 for v in vals), d.variable_id


def test_ratio_matches_its_components(olist):
    r = olist.query(_req(GetAggregate, olist, variables=("late_share", "late_deliveries_count", "deliveries_count"),
                         window=WIN, request="get_aggregate"))
    share, late, total = (o.payload for o in r.observations)
    assert share.numerator == late.total and share.denominator == total.total
