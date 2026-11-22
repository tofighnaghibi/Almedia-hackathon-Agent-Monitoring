import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.fixture_db import make_fixture_db  # noqa: E402


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    return make_fixture_db(tmp_path_factory.mktemp("db") / "market.db", days=7)


@pytest.fixture
def graph():
    from brain.graph import MetricGraph
    return MetricGraph.load()


@pytest.fixture
def store(db_path):
    from brain.data import Store
    return Store(db_path)
