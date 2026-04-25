"""Sessions endpoints — list / messages-pass-through / rename / soft-delete.

TASK-28 covers four user-scoped endpoints under `/api/v1/ncmu/sessions/*`.
All four enforce ownership at the SQL layer: the WHERE clause matches
`user_id = jwt.sub`, and "exists but you don't own it" returns the same
404 shape as "doesn't exist" — the row is invisible to anyone else
(C-4 修订, design §2.3 不变量, AC#5).

Why 404 instead of 403:
  Returning 403 on a foreign session_id would let an attacker enumerate
  which UUIDs exist by comparing 403 vs 404; collapsing both to 404
  removes that side channel.

Notes / Phase boundaries:
  - The messages endpoint forwards to Dify `/v1/messages` via the
    lifespan-owned httpx singleton + the App API token from settings.
    Phase 1 has a single [KB] App so DIFY_APP_DEFAULT_TOKEN suffices;
    Phase 3 will need a per-App token map (推 TASK-PLAN-FIX-2 / Phase 3
    admin UI — flagged in plan §15).
  - PATCH bumps `updated_at` so the renamed session resurfaces at the
    top of the list view (design §3.2 — list ordered by updated_at DESC).
  - DELETE is *soft*: we set `deleted_at` only; the row is then filtered
    by every read path. Hard delete is out of scope for Phase 1.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.auth.deps import CurrentUser, get_current_user
from ncmu_backend.config import Settings, get_settings
from ncmu_backend.db.models import ChatSession
from ncmu_backend.db.session import get_db
from ncmu_backend.schemas.sessions import (
    MessagesOut,
    SessionListOut,
    SessionOut,
    SessionPatchRequest,
)

router = APIRouter(tags=["sessions"])
log = logging.getLogger("ncmu_backend.sessions")

# Single NCMU code for "session not found OR you don't own it" — the
# response body is identical so an attacker can't tell the two cases
# apart (cf. module docstring on side-channel hardening).
_CODE_SESSION_NOT_FOUND = 1300


def _resolve_dify_client(request: Request) -> httpx.AsyncClient:
    """Look up the lifespan-owned httpx client for Dify upstream calls.

    Looked up *lazily* (called after ownership checks) rather than via
    Depends so a missing client surfaces as 503 only on paths that
    actually need Dify; ownership-rejected requests get a clean 404
    independently of upstream availability.
    """
    client = getattr(request.app.state, "dify_client", None)
    if client is None:
        # Lifespan didn't run — should be impossible in production but
        # can occur in tests that bypass lifespan handling.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": 9001, "message": "dify client not initialised"},
        )
    return client


async def _load_owned_session(
    session_id: UUID,
    user: CurrentUser,
    db: AsyncSession,
) -> ChatSession:
    """Fetch a `ChatSession` IFF it belongs to the caller AND isn't soft-deleted.

    Raises 404 with code 1300 in every "not yours / doesn't exist /
    deleted" case so callers can't enumerate IDs.
    """
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == UUID(user.sub),
            ChatSession.deleted_at.is_(None),
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": _CODE_SESSION_NOT_FOUND, "message": "session not found"},
        )
    return session


# --------------------------------------------------------------------- AC#1
@router.get(
    "/api/v1/ncmu/sessions",
    response_model=SessionListOut,
    summary="List my chat sessions (newest first)",
)
async def list_my_sessions(
    app_id: Optional[str] = Query(default=None, description="Filter to one Dify App's sessions"),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: Optional[str] = Query(
        default=None,
        description="ISO-8601 updated_at of the last item from the previous page",
    ),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionListOut:
    stmt = (
        select(ChatSession)
        .where(
            ChatSession.user_id == UUID(user.sub),
            ChatSession.deleted_at.is_(None),
        )
        .order_by(desc(ChatSession.updated_at), desc(ChatSession.id))
    )
    if app_id:
        stmt = stmt.where(ChatSession.dify_app_id == app_id)
    if cursor:
        try:
            cursor_dt = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": 1301, "message": "invalid cursor (must be ISO-8601 updated_at)"},
            )
        stmt = stmt.where(ChatSession.updated_at < cursor_dt)
    # +1 to know if there's a next page without a separate count query.
    stmt = stmt.limit(limit + 1)

    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor = (
        rows[-1].updated_at.isoformat() if rows and has_more else None
    )
    return SessionListOut(
        items=[SessionOut.model_validate(r) for r in rows],
        next_cursor=next_cursor,
    )


# --------------------------------------------------------------------- AC#2
@router.get(
    "/api/v1/ncmu/sessions/{session_id}/messages",
    response_model=MessagesOut,
    summary="List messages for one session (passthrough Dify /v1/messages)",
)
async def list_session_messages(
    session_id: UUID,
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: Optional[str] = Query(default=None, description="Dify message UUID to page from"),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MessagesOut:
    # Ownership check first — a foreign session_id returns 404 even if
    # Dify is down, so the response shape never leaks "this row exists".
    session = await _load_owned_session(session_id, user, db)
    # Resolve dify lazily (only on the happy path).
    dify = _resolve_dify_client(request)

    params = {
        "conversation_id": session.dify_conversation_id,
        "user": user.sub,
        "limit": limit,
    }
    if cursor:
        params["first_id"] = cursor

    try:
        resp = await dify.get(
            f"{settings.DIFY_BASE_URL}/v1/messages",
            params=params,
            headers={"Authorization": f"Bearer {settings.DIFY_APP_DEFAULT_TOKEN}"},
            timeout=30.0,
        )
    except httpx.RequestError as exc:
        log.warning("Dify /v1/messages unreachable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": 9001, "message": "Dify upstream unreachable"},
        ) from exc

    if resp.status_code != 200:
        log.warning(
            "Dify /v1/messages returned %s for conv %s",
            resp.status_code, session.dify_conversation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": 9001, "message": f"Dify upstream {resp.status_code}"},
        )

    payload = resp.json()
    return MessagesOut(
        data=payload.get("data", []),
        has_more=bool(payload.get("has_more", False)),
        limit=payload.get("limit", limit),
    )


# --------------------------------------------------------------------- AC#3
@router.patch(
    "/api/v1/ncmu/sessions/{session_id}",
    response_model=SessionOut,
    summary="Rename one of my sessions",
)
async def rename_session(
    session_id: UUID,
    req: SessionPatchRequest,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionOut:
    session = await _load_owned_session(session_id, user, db)
    session.title = req.title
    session.updated_at = datetime.now(timezone.utc)  # bump so it surfaces in list
    await db.commit()
    await db.refresh(session)
    return SessionOut.model_validate(session)


# --------------------------------------------------------------------- AC#4
@router.delete(
    "/api/v1/ncmu/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete one of my sessions",
)
async def soft_delete_session(
    session_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    session = await _load_owned_session(session_id, user, db)
    now = datetime.now(timezone.utc)
    session.deleted_at = now
    session.updated_at = now
    await db.commit()
    return None
