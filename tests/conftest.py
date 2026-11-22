import pytest

from mkt import registry
from mkt.config import Config


@pytest.fixture(scope="session")
def cfg():
    return Config.load()


@pytest.fixture(scope="session")
def params(cfg):
    from mkt import pipeline
    try:
        registry.load("olist", cfg.artifacts)
    except registry.MissingSource:
        pytest.skip("Olist not registered: run `mkt source add olist` first")
    return pipeline.params(cfg)[0]
