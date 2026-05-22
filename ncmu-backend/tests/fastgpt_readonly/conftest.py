"""Shared fixtures for fastgpt_readonly/ tests.

The module-level ``_metadata_cache`` in ``fastgpt_readonly.client`` is a
process-wide singleton (plan §4.3 AC#4 / REWORK-PC2-B-INDEP C1) — tests
that swap the upstream transport between cases would otherwise see
phantom hits from earlier cases. Clearing the cache + the FastAPI
process-singleton client before every test keeps the suite hermetic.
"""
from __future__ import annotations

# Force ``ncmu_backend.main`` to load first so its auto-discovery loop
# fully populates ``sys.modules`` before we import the route module
# below. Without this, importing fastgpt_readonly.routes here triggers a
# circular chain (fastgpt_readonly.routes → apps.routes → main →
# discovery → admin.routes → apps.routes-partial) that surfaces as an
# ``ImportError: cannot import name 'get_dify_console_client'``. In a
# real uvicorn boot main is the entry point so the chain unwinds in the
# normal order — this shim restores that ordering for pytest.
import ncmu_backend.main  # noqa: F401

import pytest

from ncmu_backend.fastgpt_readonly import client as client_module
from ncmu_backend.fastgpt_readonly.routes import _get_fastgpt_client_singleton


@pytest.fixture(autouse=True)
def _reset_fastgpt_readonly_module_state():
    """Drop cached metadata + the lru-cached FastGPTReadOnlyClient before
    every test so cross-test contamination is impossible."""
    client_module._metadata_cache.clear()
    _get_fastgpt_client_singleton.cache_clear()
    yield
    client_module._metadata_cache.clear()
    _get_fastgpt_client_singleton.cache_clear()
