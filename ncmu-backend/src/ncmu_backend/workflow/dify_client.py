"""Dify HTTP/SSE async client — used by every non-chat orchestrator
(advanced-chat / completion / workflow / agent-chat).

Why a dedicated thin client:
- The chat orchestrator (Phase 1) talks to Dify via the lifespan-owned
  `httpx.AsyncClient` injected through `Depends(get_dify_client)`.
  Phase 2B's per-mode orchestrators construct their own short-lived
  client per `run()` invocation so request scope == DB transaction
  scope; the lifespan singleton is reserved for the chat hot path.
- Centralising line-parsing here means each orchestrator subclass only
  has to map Dify's per-event JSON onto the NCMU schema, not also
  handle SSE framing.

The SSE rules implemented:
- Only `data: ` prefixed lines are forwarded; `: heartbeat` comments
  and blank lines are dropped at the source.
- `data: [DONE]` is the upstream end-of-stream marker — skipped (the
  caller's `async for` loop terminates naturally when the response
  closes).
- `json.JSONDecodeError` on a `data: ` line is silently skipped: Dify
  occasionally emits half-flushed frames during an upstream restart;
  bubbling the error up would crash an otherwise-recoverable run.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx


class DifyStreamClient:
    """Single-purpose async SSE consumer for Dify v1.13.3 endpoints."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._timeout = timeout

    async def stream(
        self,
        path: str,
        body: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        """POST `body` to `base_url + path` and yield each parsed SSE frame
        as a dict. Skips heartbeats / [DONE] / malformed payloads.
        """
        url = f"{self._base}{path}"
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream("POST", url, json=body, headers=headers) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[len("data: "):].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        yield json.loads(payload)
                    except json.JSONDecodeError:
                        continue
