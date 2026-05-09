"""TASK-69 AC#1 — ModeDispatcher unit tests (≥4 cases).

Pure unit-level (no FastAPI / no DB) — register / dispatch / resolve_mode
behaviours are exercised against in-memory mocks.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock

import pytest

from ncmu_backend.schemas.sse_events import NcmuSseEvent
from ncmu_backend.workflow._base import BaseOrchestrator
from ncmu_backend.workflow.mode_dispatcher import ModeDispatcher


class _FakeOrchestrator(BaseOrchestrator):
    """Yields a single ``ping`` event so ``dispatch(...)`` is iterable."""

    mode = "advanced-chat"

    def __init__(self):
        # Skip BaseOrchestrator.__init__ to avoid needing a DifyStreamClient.
        pass

    async def run(
        self,
        run_id: uuid.UUID,
        app_id: str,
        user_id: uuid.UUID,
        inputs: dict[str, Any],
    ) -> AsyncIterator[NcmuSseEvent]:
        yield NcmuSseEvent(
            event_type="ping",
            run_id=run_id,
            timestamp=datetime.now(timezone.utc),
            data={"app_id": app_id},
        )


def _make_dispatcher() -> ModeDispatcher:
    """ModeDispatcher with a stub DifyStreamClient — dispatcher ignores it."""
    from ncmu_backend.workflow.dify_client import DifyStreamClient

    return ModeDispatcher(DifyStreamClient(base_url="http://dify-api:5001", api_key="k"))


# --------------------------------------------------------------------- #
# (a) register + dispatch yields events from the registered orchestrator
# --------------------------------------------------------------------- #
async def test_register_then_dispatch_yields_events():
    dispatcher = _make_dispatcher()
    orch = _FakeOrchestrator()
    dispatcher.register("advanced-chat", orch)

    run_id = uuid.uuid4()
    user_id = uuid.uuid4()
    events = []
    async for evt in dispatcher.dispatch(
        "advanced-chat", run_id, "app-test-1", user_id, {"q": "hello"}
    ):
        events.append(evt)

    assert len(events) == 1
    assert events[0].event_type == "ping"
    assert events[0].run_id == run_id
    assert events[0].data == {"app_id": "app-test-1"}


# --------------------------------------------------------------------- #
# (b) dispatch(mode='chat') raises ValueError
# --------------------------------------------------------------------- #
async def test_dispatch_chat_mode_raises_value_error():
    dispatcher = _make_dispatcher()
    with pytest.raises(ValueError, match="mode=chat must use chat orchestrator path"):
        async for _ in dispatcher.dispatch(
            "chat", uuid.uuid4(), "app-x", uuid.uuid4(), {}
        ):
            pass


# --------------------------------------------------------------------- #
# (c) dispatch(mode=unknown) raises KeyError with "B2 task pending"
# --------------------------------------------------------------------- #
async def test_dispatch_unregistered_mode_raises_key_error():
    dispatcher = _make_dispatcher()
    with pytest.raises(KeyError, match="B2 task pending"):
        async for _ in dispatcher.dispatch(
            "advanced-chat", uuid.uuid4(), "app-x", uuid.uuid4(), {}
        ):
            pass


# --------------------------------------------------------------------- #
# (d) resolve_mode for unknown app_id raises ValueError
# --------------------------------------------------------------------- #
async def test_resolve_mode_unknown_app_raises_value_error():
    dispatcher = _make_dispatcher()
    # Stub AsyncSession.execute → result.first() returns None.
    fake_db = AsyncMock()
    fake_result = AsyncMock()
    fake_result.first = AsyncMock(return_value=None)
    fake_db.execute = AsyncMock(return_value=fake_result)
    # ``result.first()`` is sync on real SQLAlchemy Result → swap to plain Mock.
    from unittest.mock import MagicMock
    fake_result.first = MagicMock(return_value=None)

    with pytest.raises(ValueError, match="not found"):
        await dispatcher.resolve_mode(fake_db, "nonexistent-app")


# --------------------------------------------------------------------- #
# (e) extra: register same mode twice raises ValueError
# --------------------------------------------------------------------- #
async def test_register_duplicate_raises_value_error():
    dispatcher = _make_dispatcher()
    orch1 = _FakeOrchestrator()
    orch2 = _FakeOrchestrator()
    dispatcher.register("advanced-chat", orch1)
    with pytest.raises(ValueError, match="already registered"):
        dispatcher.register("advanced-chat", orch2)
