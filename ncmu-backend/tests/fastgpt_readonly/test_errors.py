"""translate_to_http_exception unit tests — TASK-BNN-C (B-NEW-NEW-C).

Verifies the 5 FastGPTError → HTTPException paths each produce the
dict-shape detail ``{"code": <int>, "message": <str>}`` per the BUG-6
cross-module convention. Codes 2001–2005 are reserved for FastGPT
upstream error translation (1000-series is permission denial).
"""
from __future__ import annotations

from fastapi import status

from ncmu_backend.fastgpt_readonly.errors import (
    FastGPTNotFound,
    FastGPTServerError,
    FastGPTUnauthorized,
    FastGPTUnknownError,
    FastGPTUnreachable,
    translate_to_http_exception,
)


def test_unreachable_translates_to_503_code_2001():
    http_exc = translate_to_http_exception(FastGPTUnreachable("conn refused"))
    assert http_exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert http_exc.detail == {
        "code": 2001,
        "message": "知识库服务暂时不可用，请稍后再试",
    }


def test_not_found_translates_to_404_code_2002():
    http_exc = translate_to_http_exception(FastGPTNotFound("dataset removed"))
    assert http_exc.status_code == status.HTTP_404_NOT_FOUND
    assert http_exc.detail == {
        "code": 2002,
        "message": "文件已被管理员移除",
    }


def test_unauthorized_translates_to_500_code_2003():
    http_exc = translate_to_http_exception(FastGPTUnauthorized("bad api key"))
    assert http_exc.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert http_exc.detail == {
        "code": 2003,
        "message": "知识库配置异常，请联系管理员",
    }


def test_server_error_translates_to_500_code_2004():
    http_exc = translate_to_http_exception(FastGPTServerError("upstream 502"))
    assert http_exc.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert http_exc.detail == {
        "code": 2004,
        "message": "知识库服务异常",
    }


def test_unknown_error_translates_to_500_code_2005():
    http_exc = translate_to_http_exception(FastGPTUnknownError("418 teapot"))
    assert http_exc.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert http_exc.detail == {
        "code": 2005,
        "message": "知识库服务异常",
    }
