"""TASK-27 — POST /api/v1/ncmu/chat/{app_id} SSE relay tests.

Eight tests cover the AC matrix in plan TASK-27:

1. AC#1 — 401 without JWT
2. AC#2 — happy path: INSERT chat_sessions + ncmu.session_created + relay
3. AC#3 — continuation with valid + non-existent conversation_id
4. AC#5 — DB INSERT failure surfaces as ncmu error/9001
5. AC#4.g — Dify error frame → ncmu error/9001 + α return
6. AC#7.d — three Dify frames same conversation_id → exactly one INSERT
7. AC#7.e v1 — heartbeat ticker emits ``: ping`` ≥ 2 times during a slow upstream
8. AC#7.e v2 — α: error frame closes the stream immediately, not at Dify EOF

Real Dify SSE bytes from
``NCMU-Wiki/sources/phase1/dify-chat-sample-2026-04-25/sse-sample-error-llm-invalid-param.txt``
drive the AC#4.g test so the error-code mapping is anchored to a real frame.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from ncmu_backend.db.models import ChatSession


def _locate_wiki_sample(name: str) -> bytes:
    """Find a TASK-24 captured Dify SSE sample.

    Resolution order:
      1. ``$NCMU_WIKI_PATH`` env var (used by the dockerized test runner
         to point at a bind-mounted wiki).
      2. Host repo layout: ``<repo>/NCMU-Wiki`` (sibling of NCMU/).
    """
    candidates: list[Path] = []
    if env := os.environ.get("NCMU_WIKI_PATH"):
        candidates.append(Path(env))
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidates.append(parent / "NCMU-Wiki")
        candidates.append(parent / "NCMU_Proj" / "NCMU-Wiki")
    for root in candidates:
        target = (root / "sources" / "phase1"
                  / "dify-chat-sample-2026-04-25" / name)
        if target.is_file():
            return target.read_bytes()
    raise FileNotFoundError(
        f"Could not locate Dify SSE sample {name!r}; tried: "
        + ", ".join(str(c) for c in candidates)
    )


_REAL_INVALID_PARAM_FRAME = _locate_wiki_sample(
    "sse-sample-error-llm-invalid-param.txt"
)


# A second copy of the FakeDifyClient skeleton — the conftest fixture is
# wired to FastAPI's dependency_overrides, but two tests below bypass the
# HTTP layer and pass a fake directly to ``stream_chat``. Duplicating
# rather than importing avoids forcing ``tests/`` onto sys.path.
class _LocalFakeStreamCM:
    def __init__(self, holder):
        self._h = holder

    async def __aenter__(self):
        return _LocalFakeResp(self._h)

    async def __aexit__(self, *_exc):
        return False


class _LocalFakeResp:
    def __init__(self, h):
        self._h = h
        self.status_code = h.get("status", 200)

    async def aiter_bytes(self):
        b = self._h["sse_bytes"]
        sz = self._h.get("chunk_size", 4096)
        delay = self._h.get("chunk_delay_s", 0.0)
        for i in range(0, len(b), sz):
            if delay:
                await asyncio.sleep(delay)
            yield b[i:i + sz]


class _LocalFakeClient:
    def __init__(self, sse_bytes, *, chunk_size=4096, chunk_delay_s=0.0):
        self.holder = {
            "sse_bytes": sse_bytes, "status": 200,
            "chunk_size": chunk_size, "chunk_delay_s": chunk_delay_s,
        }

    def stream(self, method, url, **kwargs):
        return _LocalFakeStreamCM(self.holder)


def _make_message_frame(conv_id: str, msg_id: str, answer: str,
                        event: str = "message") -> bytes:
    payload = {
        "event": event,
        "conversation_id": conv_id,
        "message_id": msg_id,
        "answer": answer,
    }
    return f"data: {json.dumps(payload)}\n\n".encode("utf-8")


def _make_message_end(conv_id: str, msg_id: str) -> bytes:
    payload = {
        "event": "message_end",
        "conversation_id": conv_id,
        "message_id": msg_id,
        "metadata": {"retriever_resources": [], "usage": {}},
    }
    return f"data: {json.dumps(payload)}\n\n".encode("utf-8")


def _decode_sse_events(raw: bytes) -> list[dict]:
    """Parse the orchestrator's outbound SSE bytes into a list of frames.

    Comment frames (``: ping``) are returned with ``event="<comment>"`` so
    the heartbeat test can count them; ``data:`` payloads are JSON-decoded.
    """
    out: list[dict] = []
    for chunk in raw.decode("utf-8", errors="replace").split("\n\n"):
        if not chunk.strip():
            continue
        evt = "<comment>" if chunk.lstrip().startswith(":") else None
        data: dict | None = None
        for line in chunk.splitlines():
            if line.startswith("event: "):
                evt = line[7:].strip()
            elif line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                except json.JSONDecodeError:
                    pass
        out.append({"event": evt or "message", "data": data})
    return out


_TEST_USER_UUID = "a0000001-0000-4000-8000-000000000001"


# -------------------------------------------------------------------- AC#1


async def test_chat_no_jwt_returns_401(app_client, fake_dify):
    r = await app_client.post(
        "/api/v1/ncmu/chat/test-app-id",
        json={"query": "hi"},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == 1102


# -------------------------------------------------------------------- AC#2


async def test_chat_happy_inserts_session_and_emits_session_created(
    app_client, jwt_token, fake_dify, async_db,
):
    conv_id = "conv-happy-1"
    msg_id = "msg-happy-1"
    payload = (
        _make_message_frame(conv_id, msg_id, "hello ")
        + _make_message_frame(conv_id, msg_id, "world")
        + _make_message_end(conv_id, msg_id)
    )
    fake_dify.set_sse(payload)

    async with app_client.stream(
        "POST",
        "/api/v1/ncmu/chat/dify-app-001",
        headers={"Authorization": f"Bearer {jwt_token}"},
        json={"query": "hello?"},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = b""
        async for chunk in resp.aiter_bytes():
            body += chunk

    frames = _decode_sse_events(body)
    events = [f["event"] for f in frames]

    # First non-comment frame must be ncmu.session_created
    non_comment = [f for f in frames if f["event"] != "<comment>"]
    assert non_comment[0]["event"] == "ncmu.session_created"
    assert non_comment[0]["data"]["dify_conversation_id"] == conv_id
    assert uuid.UUID(non_comment[0]["data"]["session_id"])  # parses as UUID

    # Subsequent message + message_end frames are forwarded
    assert "message" in events
    assert "message_end" in events

    # DB has exactly one row for this user + conv_id
    result = await async_db.execute(
        select(ChatSession).where(ChatSession.dify_conversation_id == conv_id)
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    assert str(rows[0].user_id) == _TEST_USER_UUID
    assert rows[0].dify_app_id == "dify-app-001"


# -------------------------------------------------------------------- AC#3


async def test_chat_resume_with_valid_and_invalid_conversation_id(
    app_client, jwt_token, fake_dify, async_db,
):
    """AC#3.a: bad conv_id → ncmu error/9001 frame; AC#3.b: valid conv_id →
    transparent relay, no second INSERT."""
    # Pre-create a chat_session owned by the JWT subject.
    pre_conv_id = "conv-pre-existing-1"
    new_session = ChatSession(
        id=uuid.uuid4(),
        user_id=uuid.UUID(_TEST_USER_UUID),
        dify_app_id="dify-app-002",
        dify_conversation_id=pre_conv_id,
        title="pre",
    )
    async_db.add(new_session)
    await async_db.commit()
    await async_db.refresh(new_session)
    pre_session_id = new_session.id

    # ----- AC#3.a: a conv_id we don't own → error/9001 -----
    fake_dify.set_sse(b"")  # should never be hit
    async with app_client.stream(
        "POST",
        "/api/v1/ncmu/chat/dify-app-002",
        headers={"Authorization": f"Bearer {jwt_token}"},
        json={"query": "resume?", "conversation_id": "totally-bogus"},
    ) as resp:
        assert resp.status_code == 200
        body = b""
        async for chunk in resp.aiter_bytes():
            body += chunk
    frames = _decode_sse_events(body)
    non_comment = [f for f in frames if f["event"] != "<comment>"]
    assert len(non_comment) == 1
    assert non_comment[0]["event"] == "error"
    assert non_comment[0]["data"]["code"] == 9001

    # ----- AC#3.b: valid resume → transparent relay, no extra INSERT -----
    fake_dify.set_sse(
        _make_message_frame(pre_conv_id, "msg-resume", "resumed")
        + _make_message_end(pre_conv_id, "msg-resume")
    )
    async with app_client.stream(
        "POST",
        "/api/v1/ncmu/chat/dify-app-002",
        headers={"Authorization": f"Bearer {jwt_token}"},
        json={"query": "resume", "conversation_id": pre_conv_id},
    ) as resp:
        body = b""
        async for chunk in resp.aiter_bytes():
            body += chunk
    frames = _decode_sse_events(body)
    events = [f["event"] for f in frames if f["event"] != "<comment>"]
    assert "ncmu.session_created" not in events  # no double-insert
    assert "message" in events
    assert "message_end" in events

    # Still exactly one row for that conv_id
    result = await async_db.execute(
        select(ChatSession).where(ChatSession.dify_conversation_id == pre_conv_id)
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].id == pre_session_id


# -------------------------------------------------------------------- AC#5


async def test_chat_db_insert_failure_emits_9001(
    app_client, jwt_token, fake_dify, async_db,
):
    """Forcing AsyncSession.commit to raise on the FIRST INSERT only must
    surface as an SSE error/9001 frame and the relay must terminate."""
    conv_id = "conv-insert-fail-1"
    msg_id = "msg-fail-1"
    fake_dify.set_sse(_make_message_frame(conv_id, msg_id, "x"))

    from sqlalchemy.ext.asyncio import AsyncSession

    real_commit = AsyncSession.commit
    call_count = {"n": 0}

    async def flaky_commit(self):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated commit failure")
        return await real_commit(self)

    with patch.object(AsyncSession, "commit", flaky_commit):
        async with app_client.stream(
            "POST",
            "/api/v1/ncmu/chat/dify-app-003",
            headers={"Authorization": f"Bearer {jwt_token}"},
            json={"query": "hi"},
        ) as resp:
            assert resp.status_code == 200
            body = b""
            async for chunk in resp.aiter_bytes():
                body += chunk

    frames = _decode_sse_events(body)
    non_comment = [f for f in frames if f["event"] != "<comment>"]
    assert any(f["event"] == "error" and f["data"]["code"] == 9001
               for f in non_comment)
    assert not any(f["event"] == "ncmu.session_created" for f in non_comment)

    # Nothing landed in the DB
    result = await async_db.execute(
        select(ChatSession).where(ChatSession.dify_conversation_id == conv_id)
    )
    assert result.scalars().first() is None


# -------------------------------------------------------------------- AC#4.g


async def test_chat_dify_error_frame_maps_to_9001_and_alpha_returns(
    app_client, jwt_token, fake_dify,
):
    """Replay the real `code: invalid_param` Dify frame from TASK-24 capture.
    Expect: ncmu error/9001 (mapped from the Dify string code) carrying the
    upstream `message` field, and α — no further frames after the error."""
    # Tail of the file may have trailing payload; the real sample ends with
    # the error frame. Wrap with a trailing "garbage" message that should
    # NEVER reach the SPA because α `return`s on error.
    trailing_garbage = _make_message_frame(
        "should-not-arrive", "should-not-arrive", "leaked!"
    )
    fake_dify.set_sse(_REAL_INVALID_PARAM_FRAME + trailing_garbage)

    async with app_client.stream(
        "POST",
        "/api/v1/ncmu/chat/dify-app-llmerr",
        headers={"Authorization": f"Bearer {jwt_token}"},
        json={"query": "max_tokens=99999999"},
    ) as resp:
        body = b""
        async for chunk in resp.aiter_bytes():
            body += chunk

    frames = _decode_sse_events(body)
    non_comment = [f for f in frames if f["event"] != "<comment>"]

    # Last data frame must be the mapped error
    error_frames = [f for f in non_comment if f["event"] == "error"]
    assert len(error_frames) == 1
    err = error_frames[0]
    assert err["data"]["code"] == 9001
    # Upstream message field is forwarded (not lost)
    assert "max_tokens" in err["data"]["message"]

    # α: trailing_garbage's "leaked!" must not appear anywhere
    assert b"leaked!" not in body
    # No frames AFTER the error (other than possibly a heartbeat comment,
    # which would be a `<comment>` event — already filtered out).
    assert non_comment[-1]["event"] == "error"


# -------------------------------------------------------------------- AC#7.d


async def test_chat_three_frames_same_conversation_inserts_once(
    app_client, jwt_token, fake_dify, async_db,
):
    conv_id = "conv-uniqueness-1"
    payload = (
        _make_message_frame(conv_id, "m1", "a")
        + _make_message_frame(conv_id, "m1", "b")
        + _make_message_frame(conv_id, "m1", "c")
        + _make_message_end(conv_id, "m1")
    )
    fake_dify.set_sse(payload)

    async with app_client.stream(
        "POST",
        "/api/v1/ncmu/chat/dify-app-uniq",
        headers={"Authorization": f"Bearer {jwt_token}"},
        json={"query": "?"},
    ) as resp:
        body = b""
        async for chunk in resp.aiter_bytes():
            body += chunk

    frames = _decode_sse_events(body)
    session_created_count = sum(
        1 for f in frames if f["event"] == "ncmu.session_created"
    )
    assert session_created_count == 1

    result = await async_db.execute(
        select(ChatSession).where(ChatSession.dify_conversation_id == conv_id)
    )
    rows = result.scalars().all()
    assert len(rows) == 1


# -------------------------------------------------------------------- AC#7.e v1


async def test_chat_heartbeat_emits_at_least_two_pings(async_db):
    """Direct stream_chat invocation with a 50 ms heartbeat against a slow
    Dify mock. We bypass the HTTP layer here because the FastAPI route
    intentionally hard-codes the production 20 s interval — patching that
    would mean modifying the route purely for a test signal."""
    from ncmu_backend.chat.orchestrator import stream_chat

    conv_id = "conv-heartbeat-1"
    msg_id = "msg-heartbeat-1"
    # Several frames, each delayed long enough that ~3 ticker pings fire.
    payload = (
        _make_message_frame(conv_id, msg_id, "1")
        + _make_message_frame(conv_id, msg_id, "2")
        + _make_message_frame(conv_id, msg_id, "3")
        + _make_message_end(conv_id, msg_id)
    )

    # 80 bytes per chunk and a 60 ms delay per chunk → ≥ 4 chunks → ≥ ~240 ms
    # of stream time, which at heartbeat=50ms yields ≥ 4 pings.
    fake = _LocalFakeClient(payload, chunk_size=80, chunk_delay_s=0.06)

    out: list[bytes] = []
    async for item in stream_chat(
        db=async_db,
        user_id=_TEST_USER_UUID,
        app_id="dify-app-heartbeat",
        query="slow",
        conversation_id=None,
        dify_api_base="http://dify-test",
        dify_app_token="tok",
        client=fake,
        heartbeat_interval_s=0.05,
    ):
        out.append(item)

    body = b"".join(out)
    # ``: ping\n\n`` is the SSE comment shape
    ping_count = len(re.findall(rb"^: ping$", body, flags=re.MULTILINE))
    assert ping_count >= 2, f"only {ping_count} pings in {body!r}"


# -------------------------------------------------------------------- AC#7.e v2


async def test_chat_alpha_closes_immediately_on_error_does_not_wait_dify_eof(
    async_db,
):
    """Mid-stream Dify error frame must trigger α: orchestrator yields the
    mapped error and stops consuming Dify, even though several `message`
    frames follow it in the upstream byte stream. We measure this by
    asserting that no message answer text from the post-error frames
    reaches the consumer."""
    from ncmu_backend.chat.orchestrator import stream_chat

    conv_id = "conv-alpha-1"
    msg_id = "msg-alpha-1"
    # Pre-error frame (legitimate), then the real Dify error frame,
    # then garbage that α should swallow.
    pre = _make_message_frame(conv_id, msg_id, "pre-")
    post = _make_message_frame(conv_id, msg_id, "POST-LEAKED-MARKER")
    fake = _LocalFakeClient(pre + _REAL_INVALID_PARAM_FRAME + post)

    out: list[bytes] = []
    async for item in stream_chat(
        db=async_db,
        user_id=_TEST_USER_UUID,
        app_id="dify-app-alpha",
        query="fail soon",
        conversation_id=None,
        dify_api_base="http://dify-test",
        dify_app_token="tok",
        client=fake,
        heartbeat_interval_s=10.0,  # never fires within test runtime
    ):
        out.append(item)

    body = b"".join(out)
    # Pre-error message arrived
    assert b"pre-" in body
    # Error frame arrived
    assert b'"code": 9001' in body
    # α: post-error garbage MUST NOT have surfaced
    assert b"POST-LEAKED-MARKER" not in body
