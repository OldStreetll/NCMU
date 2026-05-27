"""FastGPT read-only client exception taxonomy + HTTP translator.

Five classes map upstream conditions to user-facing HTTP responses per
plan §4.3 AC#3 / errata-13 §2.4. ``translate_to_http_exception`` is the
single conversion point — call sites raise the typed exception, the
route handler (or a FastAPI exception handler) re-raises the HTTPException.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException, status

log = logging.getLogger("ncmu_backend.fastgpt_readonly.errors")


class FastGPTError(Exception):
    """Base for all FastGPT read-only client failures."""


class FastGPTUnreachable(FastGPTError):
    """Container down / network error / httpx ConnectError / ReadTimeout."""


class FastGPTNotFound(FastGPTError):
    """Upstream returned 404 — file/collection no longer exists."""


class FastGPTUnauthorized(FastGPTError):
    """Upstream returned 401/403 — FASTGPT_API_KEY misconfigured."""


class FastGPTServerError(FastGPTError):
    """Upstream returned 5xx (other than reachability failures)."""


class FastGPTUnknownError(FastGPTError):
    """Unexpected condition (parse error, unhandled status code)."""


def translate_to_http_exception(exc: FastGPTError) -> HTTPException:
    """Single translation point — keeps user-facing copy in one place."""
    if isinstance(exc, FastGPTUnreachable):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": 2001, "message": "知识库服务暂时不可用，请稍后再试"},
        )
    if isinstance(exc, FastGPTNotFound):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 2002, "message": "文件已被管理员移除"},
        )
    if isinstance(exc, FastGPTUnauthorized):
        log.error("FastGPT 401/403 — check FASTGPT_API_KEY configuration: %s", exc)
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": 2003, "message": "知识库配置异常，请联系管理员"},
        )
    if isinstance(exc, FastGPTServerError):
        log.error("FastGPT 5xx upstream: %s", exc)
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": 2004, "message": "知识库服务异常"},
        )
    log.exception("FastGPT unknown error", exc_info=exc)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"code": 2005, "message": "知识库服务异常"},
    )


def classify_http_status(status_code: int, body_snippet: Optional[str] = None) -> type[FastGPTError]:
    """Pick the right exception class for an upstream HTTP status code.

    FastGPT v4.14.x quirk: auth failures arrive as HTTP 500 with a JSON
    body like ``{"code":403,"statusText":"unAuthorization"}`` — sniff the
    body so we surface the configuration error rather than mis-classifying
    it as a generic upstream 5xx.
    """
    if body_snippet:
        compact = body_snippet.replace(" ", "")
        if '"code":403' in compact or '"code":401' in compact:
            return FastGPTUnauthorized
    if status_code == 404:
        return FastGPTNotFound
    if status_code in (401, 403):
        return FastGPTUnauthorized
    if 500 <= status_code < 600:
        return FastGPTServerError
    return FastGPTUnknownError
