"""Abstract base for the 4 Phase 2B non-chat orchestrators.

Each subclass owns the per-mode Dify endpoint path + the per-mode
event mapping (Dify's frame shape → NCMU SSE schema). The base class
locks down the calling shape so `routes.py` (TASK-69) can dispatch any
mode through a single code path.

`run` is declared as an `AsyncIterator[NcmuSseEvent]` — yielding one
NCMU envelope per upstream Dify event. `routes.py` is responsible for
serialising those into the wire-level SSE frames the SPA consumes.
"""
from __future__ import annotations

import abc
import uuid
from typing import Any, AsyncIterator

from ncmu_backend.schemas.sse_events import NcmuSseEvent
from ncmu_backend.workflow.dify_client import DifyStreamClient


class BaseOrchestrator(abc.ABC):
    """Contract every TASK-71/72/73/74 orchestrator implements."""

    mode: str  # subclass overrides — used by mode_dispatcher (TASK-69)

    def __init__(self, dify_client: DifyStreamClient):
        self._dify = dify_client

    @abc.abstractmethod
    async def run(
        self,
        run_id: uuid.UUID,
        app_id: str,
        user_id: uuid.UUID,
        inputs: dict[str, Any],
    ) -> AsyncIterator[NcmuSseEvent]:
        """Yield NCMU SSE events for this run.

        Subclasses decide how to translate the Dify upstream event stream
        into NCMU envelopes (status mapping per H2, fields per the
        relevant `*Data` schema).
        """
        raise NotImplementedError
        yield  # pragma: no cover — keeps this an async generator
