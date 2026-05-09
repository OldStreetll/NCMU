"""Phase 2B workflow run request / response schemas (TASK-69).

- ``WorkflowRunRequest``  : POST body for ``/api/v1/ncmu/workflow/apps/{app_id}/run``.
- ``WorkflowRunSummary``  : item shape for ``GET /apps/{app_id}/runs`` (list — no
  node_trace, performance).
- ``WorkflowRunDetail``   : full shape for ``GET /runs/{run_id}`` (includes
  node_trace JSONB).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkflowRunRequest(BaseModel):
    """POST run body — single ``inputs`` dict the orchestrator forwards
    to Dify (per spec §3.1; advanced-chat / agent-chat wrap query into
    ``inputs.query`` per H-FRESH-1)."""

    inputs: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunSummary(BaseModel):
    """List-view item — omits node_trace + inputs/outputs to keep the
    list cheap to render (the SPA fetches the detail endpoint when the
    user opens a row)."""

    model_config = ConfigDict(from_attributes=True)

    run_id: uuid.UUID
    app_id: str
    mode: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None


class WorkflowRunDetail(BaseModel):
    """Detail-view — every column the SPA renders on the run-detail page."""

    model_config = ConfigDict(from_attributes=True)

    run_id: uuid.UUID
    app_id: str
    mode: str
    status: str
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    node_trace: list[dict[str, Any]]
    started_at: datetime
    finished_at: datetime | None = None
    error_msg: str | None = None
