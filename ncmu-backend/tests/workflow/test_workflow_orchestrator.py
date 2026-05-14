"""TASK-73 AC#3 — WorkflowOrchestrator integration tests (≥5 sub-cases).

The orchestrator wraps `DifyStreamClient.stream("/v1/workflows/run", body)`
and translates Dify v1.13.3 frame events (node_started / node_finished /
workflow_finished) into the unified `NcmuSseEvent` envelope (TASK-68
schemas).

Test seam: rather than monkeypatch httpx (TASK-68's pattern, suitable for
DifyStreamClient internals), here we inject a stub `DifyStreamClient`
subclass whose `stream()` replays the fixture frames in order. This keeps
the assertion surface focused on orchestrator field mapping, not on SSE
parsing (already covered by test_base_orchestrator.py).

Cases (per plan AC#3 a-e + AC#5 f + endpoint sanity g):
  (a) yields exactly 5 NcmuSseEvent: 2 node_started + 2 node_finished + 1 workflow_finished
  (b) NodeFinishedData.outputs.body == "ok" on the http_1 node
  (c) WorkflowFinishedData.outputs.result == "ok"
  (d) WorkflowFinishedData.total_elapsed_ms == 1600
  (e) order matches fixture (start_started → start_finished → http_started → http_finished → workflow_finished)
  (f) AC#5: ModeDispatcher.dispatch(mode="workflow") routes to WorkflowOrchestrator
  (g) sanity: orchestrator POSTs to /v1/workflows/run with the inputs/user/response_mode body
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from ncmu_backend.schemas.sse_events import (
    NcmuSseEvent,
    NodeFinishedData,
    NodeStartedData,
    WorkflowFinishedData,
)
from ncmu_backend.workflow.dify_client import DifyStreamClient
from ncmu_backend.workflow.workflow import WorkflowOrchestrator


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "workflow_dify_sse.jsonl"


def _load_fixture_frames() -> list[dict[str, Any]]:
    """Parse the JSONL fixture file once per call (5 frames expected)."""
    with FIXTURE_PATH.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class _FakeDifyStreamClient(DifyStreamClient):
    """Replay fixture frames in order; record (path, body) per call.

    Inherits from `DifyStreamClient` so WorkflowOrchestrator's `dify_client`
    parameter type-checks identically to production. Only `.stream()` is
    overridden; base `__init__` is bypassed because the fake never opens
    an HTTP connection.
    """

    def __init__(self) -> None:  # noqa: D401 — stub, no super().__init__
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._frames = _load_fixture_frames()

    async def stream(  # type: ignore[override]
        self,
        path: str,
        body: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        self.calls.append((path, body))
        for frame in self._frames:
            yield frame


@pytest.fixture
def fake_dify() -> _FakeDifyStreamClient:
    return _FakeDifyStreamClient()


@pytest.fixture
def orchestrator() -> WorkflowOrchestrator:
    """ARCH-FIX-79: orchestrator is now stateless — dispatcher (in production)
    injects the per-App client via .run()'s first arg; tests pass ``fake_dify``
    directly to .run() (see ``_collect`` below)."""
    return WorkflowOrchestrator()


@pytest.fixture
def run_kwargs() -> dict[str, Any]:
    return {
        "run_id": uuid.uuid4(),
        "app_id": "wf-test-app",
        "user_id": uuid.uuid4(),
        "inputs": {"inputs": {"question": "hi"}},
    }


def _fake_db_null_token():
    """AsyncMock db whose execute().first() returns (None,) — so
    ModeDispatcher.resolve_token falls back to the default client's token
    (ARCH-FIX-79 backward-compat path for tests that don't seed dify_apps)."""
    from unittest.mock import AsyncMock, MagicMock
    fake_db = AsyncMock()
    fake_result = MagicMock()
    fake_result.first = MagicMock(return_value=(None,))
    fake_db.execute = AsyncMock(return_value=fake_result)
    return fake_db


async def _collect(
    orch: WorkflowOrchestrator,
    dify: DifyStreamClient,
    kwargs: dict[str, Any],
) -> list[NcmuSseEvent]:
    return [evt async for evt in orch.run(dify, **kwargs)]


# --------------------------------------------------------------------- #
# (a) AC#3(a) — yields 5 events with the expected (event_type) breakdown
# --------------------------------------------------------------------- #
async def test_a_yields_five_events_with_correct_types(
    orchestrator: WorkflowOrchestrator, fake_dify: _FakeDifyStreamClient, run_kwargs: dict[str, Any]
) -> None:
    events = await _collect(orchestrator, fake_dify, run_kwargs)

    assert len(events) == 5, f"expected 5 events, got {len(events)}"
    types = [e.event_type for e in events]
    assert types.count("node_started") == 2
    assert types.count("node_finished") == 2
    assert types.count("workflow_finished") == 1


# --------------------------------------------------------------------- #
# (b) AC#3(b) — NodeFinishedData.outputs.body == "ok" on http_1 node
# --------------------------------------------------------------------- #
async def test_b_http_node_outputs_body_equals_ok(
    orchestrator: WorkflowOrchestrator, fake_dify: _FakeDifyStreamClient, run_kwargs: dict[str, Any]
) -> None:
    events = await _collect(orchestrator, fake_dify, run_kwargs)
    finished = [e for e in events if e.event_type == "node_finished"]
    http_node = next(e for e in finished if e.data.node_id == "http_1")

    assert isinstance(http_node.data, NodeFinishedData)
    assert http_node.data.outputs == {"body": "ok"}
    assert http_node.data.outputs["body"] == "ok"
    assert http_node.data.status == "succeeded"
    assert http_node.data.elapsed_ms == 1500


# --------------------------------------------------------------------- #
# (c) AC#3(c) — WorkflowFinishedData.outputs.result == "ok"
# --------------------------------------------------------------------- #
async def test_c_workflow_finished_outputs_result_equals_ok(
    orchestrator: WorkflowOrchestrator, fake_dify: _FakeDifyStreamClient, run_kwargs: dict[str, Any]
) -> None:
    events = await _collect(orchestrator, fake_dify, run_kwargs)
    wf = next(e for e in events if e.event_type == "workflow_finished")

    assert isinstance(wf.data, WorkflowFinishedData)
    assert wf.data.outputs == {"result": "ok"}
    assert wf.data.outputs["result"] == "ok"
    assert wf.data.status == "succeeded"


# --------------------------------------------------------------------- #
# (d) AC#3(d) — WorkflowFinishedData.total_elapsed_ms == 1600
# --------------------------------------------------------------------- #
async def test_d_workflow_total_elapsed_ms_equals_1600(
    orchestrator: WorkflowOrchestrator, fake_dify: _FakeDifyStreamClient, run_kwargs: dict[str, Any]
) -> None:
    events = await _collect(orchestrator, fake_dify, run_kwargs)
    wf = next(e for e in events if e.event_type == "workflow_finished")

    assert isinstance(wf.data, WorkflowFinishedData)
    assert wf.data.total_elapsed_ms == 1600


# --------------------------------------------------------------------- #
# (e) AC#3(e) — order matches fixture line order
# --------------------------------------------------------------------- #
async def test_e_event_order_matches_fixture(
    orchestrator: WorkflowOrchestrator, fake_dify: _FakeDifyStreamClient, run_kwargs: dict[str, Any]
) -> None:
    events = await _collect(orchestrator, fake_dify, run_kwargs)

    type_seq = [e.event_type for e in events]
    assert type_seq == [
        "node_started",
        "node_finished",
        "node_started",
        "node_finished",
        "workflow_finished",
    ]
    # node_id walks: start → start → http_1 → http_1 → (workflow_finished has no node_id)
    node_ids = [
        e.data.node_id
        for e in events
        if e.event_type in ("node_started", "node_finished")
    ]
    assert node_ids == ["start", "start", "http_1", "http_1"]
    # All NodeStartedData carry the title from the fixture:
    started = [e for e in events if e.event_type == "node_started"]
    assert isinstance(started[0].data, NodeStartedData)
    assert started[0].data.title == "Start"
    assert started[1].data.title == "HTTP"


# --------------------------------------------------------------------- #
# (f) AC#5 — ModeDispatcher.dispatch(mode="workflow") routes to us
# --------------------------------------------------------------------- #
async def test_f_dispatcher_dispatch_routes_to_workflow_orchestrator(
    fake_dify: _FakeDifyStreamClient, run_kwargs: dict[str, Any]
) -> None:
    from ncmu_backend.workflow.mode_dispatcher import ModeDispatcher

    # Dispatcher's own DifyStreamClient is never used by `dispatch` (M-NEW-4
    # strips db session there too); the orchestrator carries its own client.
    dispatcher = ModeDispatcher(
        DifyStreamClient(base_url="http://dify-api:5001", api_key="k")
    )
    # ARCH-FIX-79: dispatcher.get_client returns the cached default client
    # for NULL-token rows. Seed cache with our fake so the orchestrator
    # receives ``fake_dify`` at .run() time.
    dispatcher._client_cache["k"] = fake_dify
    dispatcher.register("workflow", WorkflowOrchestrator())

    events = [
        evt
        async for evt in dispatcher.dispatch(
            _fake_db_null_token(), "workflow",
            run_kwargs["run_id"],
            run_kwargs["app_id"],
            run_kwargs["user_id"],
            run_kwargs["inputs"],
        )
    ]

    assert len(events) == 5
    assert events[-1].event_type == "workflow_finished"
    assert events[0].event_type == "node_started"


# --------------------------------------------------------------------- #
# (g) sanity — endpoint path is /v1/workflows/run; body shape per plan
# --------------------------------------------------------------------- #
async def test_g_orchestrator_calls_workflows_run_endpoint_with_correct_body(
    orchestrator: WorkflowOrchestrator,
    fake_dify: _FakeDifyStreamClient,
    run_kwargs: dict[str, Any],
) -> None:
    await _collect(orchestrator, fake_dify, run_kwargs)

    assert len(fake_dify.calls) == 1, (
        f"expected exactly 1 stream() call, got {len(fake_dify.calls)}"
    )
    path, body = fake_dify.calls[0]

    assert path == "/v1/workflows/run", (
        f"workflow mode endpoint must be /v1/workflows/run (chat-messages "
        f"is the chat-mode endpoint); got {path!r}"
    )
    assert body["inputs"] == {"question": "hi"}
    assert body["user"] == str(run_kwargs["user_id"])
    assert body["response_mode"] == "streaming"


# --------------------------------------------------------------------- #
# Class-level: WorkflowOrchestrator.mode == "workflow" (also covered by
# AC#1 import smoke; in-test guard catches drift if the module is renamed)
# --------------------------------------------------------------------- #
def test_class_mode_is_workflow() -> None:
    assert WorkflowOrchestrator.mode == "workflow"


# ===================================================================== #
# TASK-B silent-drop tests (spec §3 D3 + plan §Phase 4)
# ===================================================================== #


class _StubDifyClient:
    """Test stub for ``DifyStreamClient`` — yields pre-parsed Dify frames.

    Duck-typed (no DifyStreamClient inheritance needed): orchestrators only
    call ``.stream(path, body)``. ``raise_at`` controls when (and whether)
    the stream raises — None=never, N<len=mid-stream, N≥len=after all frames.
    """

    def __init__(self, frames, raise_at=None, raise_exc=None):
        self._frames = list(frames)
        self._raise_at = raise_at
        self._raise_exc = raise_exc
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def stream(self, path, body):
        self.calls.append((path, body))
        for i, frame in enumerate(self._frames):
            if self._raise_at is not None and i == self._raise_at:
                raise self._raise_exc
            yield frame
        if (
            self._raise_at is not None
            and self._raise_at >= len(self._frames)
        ):
            raise self._raise_exc


# --------------------------------------------------------------------- #
# C1 — unknown event silently skipped + finally fall-through emits 1
# terminal envelope (plan §Phase 4)
# --------------------------------------------------------------------- #
async def test_c1_unknown_event_skipped_and_finally_emits_terminal(
    run_kwargs: dict[str, Any],
) -> None:
    frames = [
        {
            "event": "node_started",
            "data": {"node_id": "n1", "node_type": "start", "title": "Start", "inputs": None},
        },
        # Unknown event — silently skipped (no envelope).
        {"event": "future_event", "data": {}},
        {
            "event": "node_finished",
            "data": {
                "node_id": "n1",
                "node_type": "start",
                "status": "succeeded",
                "outputs": None,
            },
        },
    ]
    stub = _StubDifyClient(frames)
    orch = WorkflowOrchestrator()
    events = [evt async for evt in orch.run(stub, **run_kwargs)]

    # 2 known node events + 1 finally terminal = 3.
    assert len(events) == 3, (
        f"expected 2 known + 1 finally terminal = 3; got "
        f"{len(events)}: {[e.event_type for e in events]}"
    )
    assert [e.event_type for e in events] == [
        "node_started",
        "node_finished",
        "workflow_finished",
    ]
    terminal = events[-1]
    assert isinstance(terminal.data, WorkflowFinishedData)
    assert terminal.data.status == "exception"
    assert terminal.data.error == "Upstream stream closed without terminal event"
    # workflow mode has no accumulator → outputs always {}.
    assert terminal.data.outputs == {}


# --------------------------------------------------------------------- #
# C2 — Dify event:error → exactly 1 workflow_finished(failed)
# --------------------------------------------------------------------- #
async def test_c2_dify_event_error_yields_failed_terminal(
    run_kwargs: dict[str, Any],
) -> None:
    frames = [
        {
            "event": "error",
            "code": "invalid_param",
            "status": 400,
            "message": "max_tokens too large",
        }
    ]
    stub = _StubDifyClient(frames)
    orch = WorkflowOrchestrator()
    events = [evt async for evt in orch.run(stub, **run_kwargs)]

    assert len(events) == 1, (
        f"expected exactly 1 envelope (error returns + finally skips); "
        f"got {len(events)}: {[e.event_type for e in events]}"
    )
    terminal = events[0]
    assert terminal.event_type == "workflow_finished"
    assert isinstance(terminal.data, WorkflowFinishedData)
    assert terminal.data.status == "failed"
    assert "invalid_param" in terminal.data.error
    assert "max_tokens too large" in terminal.data.error
    # workflow mode has no accumulator → outputs always {}.
    assert terminal.data.outputs == {}


# --------------------------------------------------------------------- #
# C3 — upstream httpx.RequestError mid-stream → finally補 exception terminal
# --------------------------------------------------------------------- #
async def test_c3_upstream_exception_yields_exception_terminal(
    run_kwargs: dict[str, Any],
) -> None:
    import httpx

    # First 3 frames of fixture (2 node_started + 1 node_finished) → 3
    # normal yields; then raise → except → 1 exception terminal.
    head = _load_fixture_frames()[:3]
    stub = _StubDifyClient(
        head,
        raise_at=len(head),
        raise_exc=httpx.RequestError("upstream lost"),
    )
    orch = WorkflowOrchestrator()
    events = [evt async for evt in orch.run(stub, **run_kwargs)]

    # 3 normal envelopes + 1 exception terminal = 4.
    assert len(events) == 4, (
        f"expected 3 normal + 1 exception terminal = 4; got "
        f"{len(events)}: {[e.event_type for e in events]}"
    )
    assert [e.event_type for e in events[:3]] == [
        "node_started",
        "node_finished",
        "node_started",
    ]
    terminal = events[-1]
    assert terminal.event_type == "workflow_finished"
    assert isinstance(terminal.data, WorkflowFinishedData)
    assert terminal.data.status == "exception"
    assert "RequestError" in terminal.data.error
    assert "upstream lost" in terminal.data.error
    # workflow mode has no accumulator → outputs always {}.
    assert terminal.data.outputs == {}


# --------------------------------------------------------------------- #
# C5 — REWORK-INDEP-I-1: CancelledError must NOT yield a finally envelope
# (regression-lock for the cancelled sentinel; see test_advanced_chat C5).
# --------------------------------------------------------------------- #
async def test_c5_cancelled_error_does_not_yield_extra_envelope(
    run_kwargs: dict[str, Any],
) -> None:
    import asyncio

    # First 2 fixture frames (node_started + node_finished) → 2 normal
    # envelopes; then CancelledError on the 3rd iteration. Without the
    # ``not cancelled`` guard, finally would yield a stray
    # workflow_finished(exception) envelope AND swallow the
    # CancelledError; with the fix, events stay at 2 AND CancelledError
    # propagates to the caller.
    head = _load_fixture_frames()[:2]
    stub = _StubDifyClient(
        head,
        raise_at=len(head),
        raise_exc=asyncio.CancelledError(),
    )
    orch = WorkflowOrchestrator()

    events = []
    with pytest.raises(asyncio.CancelledError):
        async for ev in orch.run(stub, **run_kwargs):
            events.append(ev)

    assert len(events) == 2, (
        f"REWORK-INDEP-I-1: finally must NOT yield on cancellation path; "
        f"expected 2 normal envelopes; got {len(events)}: "
        f"{[e.event_type for e in events]}"
    )
    assert [e.event_type for e in events] == [
        "node_started",
        "node_finished",
    ]
    # No exception/failed terminal leaked.
    for ev in events:
        assert ev.event_type != "workflow_finished"
