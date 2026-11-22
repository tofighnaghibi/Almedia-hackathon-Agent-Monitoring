"""Shared test setup: tests never reach a real model.

The LLM switches on by itself when ANTHROPIC_API_KEY and LLM_MODEL are set,
so they are removed for the whole session, before any fixture (module-scoped
ones included) builds a monitor. Tests that need a model use mocks.
"""

import pytest

_LLM_ENV = ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "LLM_MODEL", "LLM_REPORT_MODEL", "LLM_CACHE_MODE",
            "LLM_CACHE_DIR", "LLM_TIMEOUT", "LLM_MAX_TOKENS", "LLM_EFFORT"]


@pytest.fixture(scope="session", autouse=True)
def _no_real_llm():
    mp = pytest.MonkeyPatch()
    for name in _LLM_ENV:
        mp.delenv(name, raising=False)
    yield
    mp.undo()
