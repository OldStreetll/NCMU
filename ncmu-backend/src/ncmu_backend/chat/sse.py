"""SSE encoding + Dify upstream parser.

TASK-PLAN-FIX-1 / SCOPE-CHANGE-2 修订（2026-04-25）：
Dify v1.13.3 frames are not uniform on the SSE-header layer. Real samples
captured at sources/phase1/dify-chat-sample-2026-04-25/ show two shapes:

1. ``message`` / ``message_end`` / ``error`` frames carry only a ``data:``
   line; the event type lives **inside** the JSON payload as ``data["event"]``.
   The README at the same path documents this asymmetry.
2. ``ping`` frames carry an actual ``event: ping`` SSE header line and no
   ``data:`` line at all. (Visible at the tail of ``sse-sample-happy.txt``.)

The parser therefore prefers an explicit ``event:`` header when present and
falls back to ``data["event"]`` otherwise, with a final default of
``"message"`` so a malformed frame does not desynchronise the orchestrator's
event-type dispatch.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator


def encode_sse_frame(event: str, data: dict[str, Any]) -> bytes:
    """Render a single SSE frame for the SPA: ``event: <type>\\ndata: <json>\\n\\n``.

    Bytes are returned (not str) so :class:`fastapi.responses.StreamingResponse`
    can yield them directly without an extra encode pass.
    """
    body = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {body}\n\n".encode("utf-8")


async def parse_dify_sse(
    stream: AsyncIterator[bytes],
) -> AsyncIterator[dict[str, Any]]:
    """Iterate Dify upstream SSE frames as ``{"event": str, "data": dict}``.

    Buffers raw bytes until a ``\\n\\n`` boundary, then dissects line-by-line.
    Non-JSON ``data:`` payloads are skipped silently — Dify has been observed
    to emit the occasional malformed line during back-pressure and the
    orchestrator must keep streaming.
    """
    buffer = b""
    async for chunk in stream:
        buffer += chunk
        while b"\n\n" in buffer:
            frame, buffer = buffer.split(b"\n\n", 1)
            outer_event: str | None = None
            data: Any = None
            for line in frame.decode("utf-8", errors="replace").splitlines():
                if line.startswith("event: "):
                    outer_event = line[len("event: "):].strip()
                elif line.startswith("data: "):
                    try:
                        data = json.loads(line[len("data: "):])
                    except json.JSONDecodeError:
                        # Skip malformed payload line; keep the outer header
                        # so a `ping` (no data) still surfaces.
                        pass
            if data is None and outer_event is None:
                # Empty frame (just whitespace separator) — skip.
                continue
            inner_event = (
                data.get("event") if isinstance(data, dict) else None
            )
            event_type = outer_event or inner_event or "message"
            yield {"event": event_type, "data": data if isinstance(data, dict) else {}}
