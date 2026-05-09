"""TASK-68 AC#3+#4 — DifyStreamClient + BaseOrchestrator smoke tests (≥3 cases).

DifyStreamClient creates an `httpx.AsyncClient` inside `stream()`; we
fake that with a stub class via `monkeypatch` so the parsing logic
(strip 'data: ', skip [DONE], skip malformed JSON) can be exercised
without a real Dify backend.

Cases:
  (a) AC#3 — BaseOrchestrator.__abstractmethods__ contains 'run'
  (b) AC#4(a) — `data: {...}` line → stream() yields the parsed dict
  (c) AC#4(b) — `data: [DONE]` line is skipped (no yield)
  (d) AC#4(c) — malformed JSON line is skipped (no exception bubbles up)
  (e) extra: a single stream interleaves all four flavours and yields
      only the valid JSON dicts in order
"""
from __future__ import annotations

from typing import Iterable

import pytest


# --------------------------------------------------------------------- #
# Fake httpx.AsyncClient that yields canned SSE lines without a network
# round-trip. Test sets `_FakeAsyncClient.LINES = [...]` before calling
# DifyStreamClient.stream(...).
# --------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, lines: Iterable[str]):
        self._lines = list(lines)

    def raise_for_status(self) -> None:  # mirrors httpx.Response API
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeStreamCM:
    def __init__(self, lines: Iterable[str]):
        self._lines = list(lines)

    async def __aenter__(self) -> _FakeResponse:
        return _FakeResponse(self._lines)

    async def __aexit__(self, *exc_info):
        return False


class _FakeAsyncClient:
    """Stand-in for `httpx.AsyncClient` whose `stream(...)` yields lines
    captured on `_FakeAsyncClient.LINES`. The `async with` context
    semantics are preserved so the production code path is unmodified."""

    LINES: list[str] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def stream(self, method: str, url: str, **kwargs):
        return _FakeStreamCM(_FakeAsyncClient.LINES)


@pytest.fixture(autouse=True)
def _patch_httpx(monkeypatch):
    """Swap `httpx.AsyncClient` inside `dify_client` for every test."""
    monkeypatch.setattr(
        "ncmu_backend.workflow.dify_client.httpx.AsyncClient",
        _FakeAsyncClient,
    )
    _FakeAsyncClient.LINES = []
    yield
    _FakeAsyncClient.LINES = []


# --------------------------------------------------------------------- #
# (a) AC#3 — abstract method 'run'
# --------------------------------------------------------------------- #
def test_base_orchestrator_run_is_abstract():
    from ncmu_backend.workflow._base import BaseOrchestrator

    assert "run" in BaseOrchestrator.__abstractmethods__, (
        f"BaseOrchestrator.__abstractmethods__ should include 'run'; "
        f"got {sorted(BaseOrchestrator.__abstractmethods__)}"
    )


# --------------------------------------------------------------------- #
# (b) AC#4(a) — happy path: one data line yields one dict
# --------------------------------------------------------------------- #
async def test_dify_stream_client_yields_parsed_dict():
    from ncmu_backend.workflow.dify_client import DifyStreamClient

    _FakeAsyncClient.LINES = [
        'data: {"event":"node_started","data":{"node_id":"n1"}}'
    ]
    dsc = DifyStreamClient(base_url="http://dify-api:5001", api_key="k")
    events = []
    async for ev in dsc.stream("/v1/test", {"q": "hi"}):
        events.append(ev)
    assert events == [{"event": "node_started", "data": {"node_id": "n1"}}]


# --------------------------------------------------------------------- #
# (c) AC#4(b) — `data: [DONE]` line is skipped
# --------------------------------------------------------------------- #
async def test_dify_stream_client_skips_done_marker():
    from ncmu_backend.workflow.dify_client import DifyStreamClient

    _FakeAsyncClient.LINES = ["data: [DONE]"]
    dsc = DifyStreamClient(base_url="http://dify-api:5001", api_key="k")
    events = [ev async for ev in dsc.stream("/v1/test", {})]
    assert events == []


# --------------------------------------------------------------------- #
# (d) AC#4(c) — malformed JSON is silently skipped, no exception
# --------------------------------------------------------------------- #
async def test_dify_stream_client_skips_malformed_json():
    from ncmu_backend.workflow.dify_client import DifyStreamClient

    _FakeAsyncClient.LINES = ["data: not-json-at-all", "data: }also bad{"]
    dsc = DifyStreamClient(base_url="http://dify-api:5001", api_key="k")
    events = [ev async for ev in dsc.stream("/v1/test", {})]
    assert events == []


# --------------------------------------------------------------------- #
# (e) extra — interleaved stream (heartbeat lines + DONE + bad JSON +
#     real events) should surface only the valid dicts in order
# --------------------------------------------------------------------- #
async def test_dify_stream_client_interleaved_stream():
    from ncmu_backend.workflow.dify_client import DifyStreamClient

    _FakeAsyncClient.LINES = [
        "",                                       # blank line
        ": heartbeat",                            # SSE comment (not data:)
        'data: {"event":"a"}',                    # valid
        "data: not-json",                         # malformed → skip
        "data: [DONE]",                           # done marker → skip
        'data: {"event":"b","seq":2}',            # valid
    ]
    dsc = DifyStreamClient(base_url="http://dify-api:5001", api_key="k")
    events = [ev async for ev in dsc.stream("/v1/test", {})]
    assert events == [
        {"event": "a"},
        {"event": "b", "seq": 2},
    ]
