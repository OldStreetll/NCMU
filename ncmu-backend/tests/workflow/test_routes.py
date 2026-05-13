"""TASK-69 AC#3 — workflow routes HTTP behaviour (≥4 endpoint cases +
H-FRESH-2 client-disconnect cancellation).

Mock strategy:
- ``app_client`` does NOT run lifespan, so ``app.state.workflow_dispatcher``
  is not set — the test installs its own ``ModeDispatcher`` via
  ``app.dependency_overrides[get_dispatcher]``.
- ``dify_apps`` rows are seeded directly via SQL so ``resolve_mode`` finds them.
- H-FRESH-2 cancellation: monkeypatches ``crud.finalize_run`` to record calls,
  then registers a mock orchestrator that raises ``asyncio.CancelledError``
  after the first yield to drive the ``except asyncio.CancelledError`` branch
  in ``event_stream``.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import httpx
import pytest_asyncio
from sqlalchemy import text

from ncmu_backend.schemas.sse_events import NcmuSseEvent
from ncmu_backend.workflow._base import BaseOrchestrator
from ncmu_backend.workflow.dify_client import DifyStreamClient
from ncmu_backend.workflow.mode_dispatcher import ModeDispatcher


ADMIN_USER_ID = uuid.UUID("a0000001-0000-4000-8000-000000000001")
APP_ID = "app-test-routes"
APP_ID_CHAT = "app-test-chat-mode"


# --------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def routes_client(app_client, async_db):
    """Wrap ``app_client`` so:
      - ``dify_apps`` has 1 ``advanced-chat`` row (APP_ID) + 1 ``chat`` row
        (APP_ID_CHAT) for the mode-routing matrix.
      - ``get_dispatcher`` is overridden with a fresh empty ModeDispatcher.
    """
    from ncmu_backend.main import app
    from ncmu_backend.workflow.routes import get_dispatcher

    await async_db.execute(text(
        "INSERT INTO dify_apps (dify_app_id, name, mode) VALUES "
        "(:a, 'Adv', 'advanced-chat'), (:c, 'Chat', 'chat')"
    ), {"a": APP_ID, "c": APP_ID_CHAT})
    await async_db.commit()

    dispatcher = ModeDispatcher(
        DifyStreamClient(base_url="http://dify-api:5001", api_key="k")
    )
    app.dependency_overrides[get_dispatcher] = lambda: dispatcher
    yield app_client, dispatcher
    app.dependency_overrides.pop(get_dispatcher, None)


def _bearer(jwt_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt_token}"}


# --------------------------------------------------------------------- #
# (a) POST run with non-existent app_id → 404
# --------------------------------------------------------------------- #
async def test_run_unknown_app_returns_404(routes_client, jwt_token):
    client, _ = routes_client
    resp = await client.post(
        "/api/v1/ncmu/workflow/apps/non-existent-app/run",
        json={"inputs": {}},
        headers=_bearer(jwt_token),
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------- #
# (b) POST run with mode=chat → 400 (must use /chat endpoint)
# --------------------------------------------------------------------- #
async def test_run_chat_mode_returns_400(routes_client, jwt_token):
    client, _ = routes_client
    resp = await client.post(
        f"/api/v1/ncmu/workflow/apps/{APP_ID_CHAT}/run",
        json={"inputs": {}},
        headers=_bearer(jwt_token),
    )
    assert resp.status_code == 400
    assert "chat" in resp.text.lower()


# --------------------------------------------------------------------- #
# (c) POST run with B2-unregistered mode → SSE error event code=503
# --------------------------------------------------------------------- #
async def test_run_unregistered_mode_yields_sse_error_503(
    routes_client, jwt_token, monkeypatch,
):
    client, _dispatcher = routes_client
    # Mock finalize_run — the M5 ``async with SessionLocal()`` in the
    # routes.py error branches builds independent AsyncSessions against
    # the production-like NCMU_DB_URL, not the request-overridden
    # ephemeral DB. Verifying SSE wire output is the AC; the DB write is
    # exercised by test_crud.py.
    finalize_calls: list[dict] = []

    async def fake_finalize(db, run_id, status, outputs=None, error_msg=None):
        finalize_calls.append({"run_id": run_id, "status": status,
                               "error_msg": error_msg})

    monkeypatch.setattr(
        "ncmu_backend.workflow.crud.finalize_run", fake_finalize,
    )

    async with client.stream(
        "POST",
        f"/api/v1/ncmu/workflow/apps/{APP_ID}/run",
        json={"inputs": {}},
        headers=_bearer(jwt_token),
    ) as resp:
        assert resp.status_code == 200
        body = b""
        async for chunk in resp.aiter_bytes():
            body += chunk
    text = body.decode("utf-8")
    assert "event: error" in text, text
    assert '"code": 503' in text or '"code":503' in text, text
    # KeyError branch must have flagged the run as failed.
    assert any(c["status"] == "failed" for c in finalize_calls), finalize_calls


# --------------------------------------------------------------------- #
# (d) GET list_runs → 200 + array (empty initially)
# --------------------------------------------------------------------- #
async def test_list_runs_returns_array(routes_client, jwt_token):
    client, _ = routes_client
    resp = await client.get(
        f"/api/v1/ncmu/workflow/apps/{APP_ID}/runs",
        headers=_bearer(jwt_token),
    )
    assert resp.status_code == 200
    assert resp.json() == []


# --------------------------------------------------------------------- #
# (e) GET run with unknown run_id → 404
# --------------------------------------------------------------------- #
async def test_get_run_unknown_returns_404(routes_client, jwt_token):
    client, _ = routes_client
    resp = await client.get(
        "/api/v1/ncmu/workflow/runs/00000000-0000-0000-0000-000000000000",
        headers=_bearer(jwt_token),
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------- #
# (f) DELETE run with unknown run_id → 404
# --------------------------------------------------------------------- #
async def test_delete_run_unknown_returns_404(routes_client, jwt_token):
    client, _ = routes_client
    resp = await client.delete(
        "/api/v1/ncmu/workflow/runs/00000000-0000-0000-0000-000000000000",
        headers=_bearer(jwt_token),
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------- #
# (g) H-FRESH-2 cancellation: orchestrator raises CancelledError →
# event_stream's except branch finalizes 'cancelled' + reraises.
# --------------------------------------------------------------------- #
class _CancellingOrchestrator(BaseOrchestrator):
    """Yields one ping then raises CancelledError to simulate client RST."""

    mode = "advanced-chat"

    def __init__(self):
        pass

    async def run(
        self,
        dify_client: DifyStreamClient,  # ARCH-FIX-79: dispatcher injects per-call
        run_id: uuid.UUID,
        app_id: str,
        user_id: uuid.UUID,
        inputs: dict[str, Any],
    ) -> AsyncIterator[NcmuSseEvent]:
        yield NcmuSseEvent(
            event_type="ping",
            run_id=run_id,
            timestamp=datetime.now(timezone.utc),
            data={"phase": "starting"},
        )
        # Simulate sse_starlette injecting CancelledError into our generator
        # when the client disconnects (athrow under the hood).
        raise asyncio.CancelledError()


async def test_event_stream_h_fresh_2_cancellation_finalizes_cancelled(
    routes_client, jwt_token, monkeypatch,
):
    client, dispatcher = routes_client
    dispatcher.register("advanced-chat", _CancellingOrchestrator())

    finalize_calls: list[dict] = []

    async def fake_finalize(db, run_id, status, outputs=None, error_msg=None):
        finalize_calls.append({
            "run_id": run_id,
            "status": status,
            "error_msg": error_msg,
        })

    monkeypatch.setattr(
        "ncmu_backend.workflow.crud.finalize_run", fake_finalize,
    )

    # Fire POST /run. The orchestrator raises CancelledError after the
    # first yield; the routes.py ``except asyncio.CancelledError`` branch
    # must finalize 'cancelled' then re-raise. Re-raise propagates through
    # StreamingResponse → ASGITransport (raise_app_exceptions=True default
    # in httpx 0.28+), so we wrap in try/except.
    try:
        async with client.stream(
            "POST",
            f"/api/v1/ncmu/workflow/apps/{APP_ID}/run",
            json={"inputs": {}},
            headers=_bearer(jwt_token),
        ) as resp:
            assert resp.status_code == 200
            try:
                async for _ in resp.aiter_bytes():
                    pass
            except (httpx.RemoteProtocolError, httpx.ReadError,
                    asyncio.CancelledError, AssertionError):
                pass
    except (httpx.RemoteProtocolError, httpx.ReadError,
            asyncio.CancelledError, AssertionError):
        pass

    # The except asyncio.CancelledError branch in event_stream must have
    # called finalize_run('cancelled', error_msg='client disconnect').
    cancelled = [c for c in finalize_calls if c["status"] == "cancelled"]
    assert len(cancelled) == 1, finalize_calls
    assert cancelled[0]["error_msg"] == "client disconnect"
