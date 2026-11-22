"""Fast unit tests: no data needed."""
import numpy as np
import pytest

from mkt.contracts import Intervention
from mkt.interventions import Schedule, parse_selector, selector_mask
from mkt.store import ArtifactStore


def test_selector_grammar():
    assert parse_selector("device == 'mobile' and region in ['RJ', 'SP']") == [
        ("device", "==", ["mobile"]), ("region", "in", ["RJ", "SP"])]
    attrs = {"device": np.array(["mobile", "desktop", "mobile"]), "region": np.array(["RJ", "RJ", "MG"])}
    assert selector_mask("device == 'mobile' and region != 'MG'", attrs, 3, {}).tolist() == [True, False, False]
    assert selector_mask("seller == '@A'", {"seller": np.array(["s1", "s2"])}, 2, {"@A": "s2"}).tolist() == [False, True]
    with pytest.raises(ValueError):
        parse_selector("device ~ 'x'")


def test_schedule_window_and_operators():
    from datetime import datetime
    iv = Intervention(id="i", layer="world", target="t", operator="scale", value=2.0, selector="k == 'a'",
                      window=(datetime(2020, 1, 1), datetime(2020, 1, 2)))
    s = Schedule([iv])
    t_in = int(np.datetime64("2020-01-01T12:00", "us").astype(np.int64))
    t_out = int(np.datetime64("2020-01-03T00:00", "us").astype(np.int64))
    attrs = {"k": np.array(["a", "b"])}
    assert s.apply("t", np.ones(2), attrs, t_in).tolist() == [2.0, 1.0]
    assert s.apply("t", np.ones(2), attrs, t_out).tolist() == [1.0, 1.0]
    assert s.apply("other", np.ones(2), attrs, t_in).tolist() == [1.0, 1.0]


def test_artifact_write_is_atomic(tmp_path):
    st = ArtifactStore(tmp_path)
    with st.write("stage", "k1", {"a": 1}) as d:
        (d / "x.txt").write_text("ok")
    assert st.get("stage", "k1")
    with pytest.raises(RuntimeError):
        with st.write("stage", "k1", {"a": 2}) as d:
            (d / "x.txt").write_text("partial")
            raise RuntimeError("crash mid-write")
    assert (st.get("stage", "k1") / "x.txt").read_text() == "ok"      # previous artifact intact
    assert [p.name for p in (tmp_path / "stage").iterdir()] == ["k1"]   # no temp debris
