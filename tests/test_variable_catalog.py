"""The L0-L10 variable catalogue conforms to Contracts/ and agrees with what the ports actually serve."""
import collections

import pytest

from causal_core.contracts.common import EnvironmentScope
from mkt import taxonomy as TX
from mkt.variable_catalog import CATALOG, EXCLUDED, build

SCOPE = EnvironmentScope(environment_id="catalog-check", environment_kind="emulator", generator_version="test")


def test_every_entry_is_a_valid_contract_definition():
    for v in CATALOG:
        d = build(v, SCOPE)
        assert type(d).model_validate_json(d.model_dump_json()) == d, v.variable_id


def test_ids_unique_and_all_four_kinds_present():
    dup = [k for k, n in collections.Counter(v.variable_id for v in CATALOG).items() if n > 1]
    assert not dup, dup
    assert {v.kind for v in CATALOG} == {"metric", "state", "event", "categorical"}
    assert {v.layer for v in CATALOG} >= {"L0", "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L9"}


def test_formulas_resolve_inside_the_catalogue():
    ids = {v.variable_id for v in CATALOG}
    for v in CATALOG:
        if v.kind == "metric" and v.spec["formula"]:
            assert set(v.spec["formula"]) <= ids, (v.variable_id, v.spec["formula"])
            assert v.spec["agg"] == "ratio"


def test_served_entries_match_the_served_taxonomy():
    served = {v.variable_id: v for v in CATALOG if v.status == "served" and v.entity != "service"}
    tx = {m.variable_id: ("metric", m.entity) for m in TX.METRICS} | {e.variable_id: ("event", e.entity) for e in TX.EVENTS}
    for vid, (kind, entity) in tx.items():
        assert vid in served, f"{vid} is served but missing from the catalogue"
        assert (served[vid].kind, served[vid].entity) == (kind, entity), vid


def test_privileged_ground_truth_is_never_a_variable():
    text = " ".join(f"{v.variable_id} {v.meaning}" for v in CATALOG).lower()
    for banned in ("fraud_label", "true_late", "latent", "flag_state", "twin"):
        assert banned not in text
    assert any(layer == "L8" for layer, _, _ in EXCLUDED)
