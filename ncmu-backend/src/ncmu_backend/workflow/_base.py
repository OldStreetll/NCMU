"""Abstract base for the 4 Phase 2B non-chat orchestrators.

Each subclass owns the per-mode Dify endpoint path + the per-mode
event mapping (Dify's frame shape → NCMU SSE schema). The base class
locks down the calling shape so `routes.py` (TASK-69) can dispatch any
mode through a single code path.

`run` is declared as an `AsyncIterator[NcmuSseEvent]` — yielding one
NCMU envelope per upstream Dify event. `routes.py` is responsible for
serialising those into the wire-level SSE frames the SPA consumes.

TASK-79-BACKEND-ARCH-FIX (2026-05-12): `run` accepts the per-App
``dify_client`` as its first argument. Dify v1.13.3 binds each API
token to one App; ModeDispatcher (workflow/mode_dispatcher.py) looks up
`dify_apps.api_token` at dispatch time and hands the matching cached
DifyStreamClient to the orchestrator. Orchestrators no longer hold a
client of their own — they are pure event-mapping pipelines.
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

    @abc.abstractmethod
    async def run(
        self,
        dify_client: DifyStreamClient,
        run_id: uuid.UUID,
        app_id: str,
        user_id: uuid.UUID,
        inputs: dict[str, Any],
    ) -> AsyncIterator[NcmuSseEvent]:
        """Yield NCMU SSE events for this run.

        Subclasses decide how to translate the Dify upstream event stream
        into NCMU envelopes (status mapping per H2, fields per the
        relevant `*Data` schema). ``dify_client`` is the per-App client
        chosen by ModeDispatcher.resolve_token + get_client; orchestrators
        must use it (not a stored attribute) so each request hits the
        correct Dify App.
        """
        raise NotImplementedError
        yield  # pragma: no cover — keeps this an async generator
