"""apps 业务层 — Phase 2C 双轨权限路由（shared ∪ owner）。

Paradigm shift（spec §3.3 / plan §4.4 PC2-C 字面 / C-INDEP-1 真代码 grounded）：
- shared 路径 = 既有 DifyConsoleClient real-time + 5 min lru cache
  (dify_console_client.py 字面禁区 / 5 kwargs 真签名保留)
- owner 路径 = app_owners INNER JOIN dify_apps
  (alembic 0007 PC1 + 0004/0005/0006 / ao.app_id String(64) = da.dify_app_id
  String(64) 类型对齐 / 无 CAST / spec §1.5 调和 #6)
- union + dedupe by app_id (防御 / owner App 不应被 Dify Console 返回，
  但 owner App 出现在 Dify Console 时以 shared 条目为准 / 顺序 shared 先 owner 后)

Feature flag `tags_routing_enabled` 由 PC2-D 引入 / 2D-A1 通电
（spec 2026-06-01 phase2d §3 / §6 #1）：
- flag OFF（prod 现状 / config.py:110 default False / 本批不翻）：shared 路径
  走 DifyConsoleClient real-time + `detect_app_type` 的 [KB] 名字过滤 ∪ owned。
  **prod 行为零变化**（DB tag 表完全不消费）。
- flag ON（2D-A1 新增 / 暂不上 prod / 仅测试 env 验）：shared 路径改为
  `user_tags ∩ app_tags` 交集匹配过滤（含 [KB]，统一标签控制，不再走名字
  特例）∪ owned。merge/dedupe/order 与 OFF 现状一致。翻 flag 需等 2D-A2
  同步全员标签 + 全 App 打标签后单独执行。
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.apps.dify_console_client import (
    DifyConsoleClient,
    detect_app_type,
)
from ncmu_backend.config import Settings
from ncmu_backend.db.models import AppOwner, AppTag, DifyApp, UserTag


async def query_owned_apps(
    db: AsyncSession, owner_user_id: UUID,
) -> list[dict[str, Any]]:
    """Owner 路径 SQL — plan §4.4 AC#2 (3-col projection / SPA AppOut 仅消费)。

        SELECT ao.app_id, da.name, da.mode
        FROM app_owners ao
        INNER JOIN dify_apps da ON ao.app_id = da.dify_app_id
        WHERE ao.owner_user_id = :uid AND ao.visibility = 'owner_only'

    Note: plan §4.4 AC#2 字面 SELECT 含 5 列（api_token + visibility），但
    SPA AppOut 仅消费 id/name/mode/description。WHERE 已过滤 visibility ==
    'owner_only'，无需 SELECT；api_token 是 PC2-B (fastgpt_readonly) 才用。
    SELECT 3 列是性能优化 / 0 业务影响 / WHERE 字面与 plan 一致。

    Returns Dify-Console-shape dicts (id/name/mode/description) so the
    union with shared_apps stays homogeneous. INNER JOIN ensures any
    orphan app_owners row (no matching dify_apps cache entry) is dropped
    — orphans are a sync-lag artefact, not surfaceable apps.

    JOIN 类型对齐：ao.app_id 与 da.dify_app_id 均为 sa.String(length=64)
    （spec §1.5 调和 #6 + TASK-PC1 AC#4b 落地）— 无需 CAST。
    """
    stmt = (
        select(
            AppOwner.app_id.label("app_id"),
            DifyApp.name.label("name"),
            DifyApp.mode.label("mode"),
        )
        .join(DifyApp, AppOwner.app_id == DifyApp.dify_app_id)
        .where(AppOwner.owner_user_id == owner_user_id)
        .where(AppOwner.visibility == "owner_only")
    )
    result = await db.execute(stmt)
    return [
        {
            "id": row.app_id,
            "name": row.name,
            "mode": row.mode,
            "description": None,
        }
        for row in result
    ]


async def query_user_tag_ids(
    db: AsyncSession, user_id: UUID,
) -> set[UUID]:
    """TASK-2D-A1 — 该 user 绑定的 tag_id 集合（user_tags / PE-09 表）。

        SELECT tag_id FROM user_tags WHERE user_id = :uid

    返回 set 供 flag-ON 路径做 `user_tags ∩ app_tags` 交集判定。空集表示
    该 user 未打任何标签（flag ON 下其共享 App 全不可见 / restrictive 语义）。
    与 query_owned_apps 同风格：db 首位置参 + ORM select + async execute。
    """
    stmt = select(UserTag.tag_id).where(UserTag.user_id == user_id)
    result = await db.execute(stmt)
    return {row.tag_id for row in result}


async def query_app_ids_matching_tags(
    db: AsyncSession, user_tag_ids: set[UUID],
) -> set[str]:
    """TASK-2D-A1 — 与给定 tag_id 集合有交集的 dify_app_id 集合（app_tags / PE-08 表）。

        SELECT DISTINCT dify_app_id FROM app_tags WHERE tag_id = ANY(:tag_ids)

    一次批量查代替 per-App N+1 往返（REWORK Minor #1 / 对齐 codebase
    既有 .in_() 批量范式）。caller 用返回集合内存过滤 shared_raw
    （`aid in matched`）。含 [KB] 名 App 也一视同仁——无匹配 tag 即不在
    返回集 / 无名字特例。

    前置：caller 须先判 `user_tag_ids` 非空（空集时本查询 IN () 恒空，
    caller 短路省去这趟 RTT）。与 query_owned_apps 同风格。
    """
    stmt = (
        select(AppTag.dify_app_id)
        .where(AppTag.tag_id.in_(user_tag_ids))
        .distinct()
    )
    result = await db.execute(stmt)
    return {row.dify_app_id for row in result}


async def list_apps_for_user(
    *,
    db: AsyncSession,
    user_id: str,
    http: httpx.AsyncClient,
    cache: DifyConsoleClient,
    settings: Settings,
) -> list[dict[str, Any]]:
    """返回 user 可访问的 App 列表 = shared (DifyConsoleClient + [KB] filter)
    ∪ owner (DB query 已按 owner_only scope)，dedupe by app_id。

    顺序：shared 先 / owner 后（plan §4.4 AC#1 字面）。
    Dedupe rule：shared entry 优先（Dify Console 是 source of truth；
    owner App 不应出现在 Dify Console 返回但写防御代码）。

    Shape：每个条目是 Dify-Console-shape raw dict（id/name/mode/description/...），
    供 routes.py 投影到 AppOut。

    传播：DifyConsoleClient 抛 HTTPException 时不吞 — 由 caller 决定
    上游错误是否同时让 owner 路径失败（当前实现：直接传播 / shared 5xx 时
    owner 路径不返回，符合 fail-fast 语义）。
    """
    shared_raw = await cache.list_apps_for_user(
        user_id=user_id,
        http=http,
        base_url=settings.DIFY_BASE_URL,
        api_key=settings.DIFY_CONSOLE_API_KEY,
        tenant_id=settings.DIFY_TENANT_ID,
    )
    if settings.tags_routing_enabled:
        # flag ON (2D-A1 通电 / spec §3 / §6 #1): shared = shared_raw 中
        # dify_app_id 的 app_tags 与该 user 的 user_tags 有交集者（含 [KB]，
        # 统一标签控制，不走名字特例）。
        user_tag_ids = await query_user_tag_ids(db, UUID(user_id))
        if not user_tag_ids:
            # restrictive 空集语义：user 无标签 → 交集恒空 → 共享全隐。
            # 短路省去批量查这趟 RTT（REWORK Minor #1a）。
            shared_kb = []
        else:
            # 单次批量查取「与 user 标签有交集的 app_id 集合」，再内存过滤
            # shared_raw（REWORK Minor #1b / N+1 → 1 query）。
            matched_app_ids = await query_app_ids_matching_tags(db, user_tag_ids)
            shared_kb = [
                a for a in shared_raw
                if a.get("id") and a["id"] in matched_app_ids
            ]
    else:
        # flag OFF (prod 现状 / 本批一字不改 / DB tag 表不消费): [KB] 名字过滤。
        shared_kb = [
            a for a in shared_raw
            if detect_app_type(a.get("name") or "", a.get("tags"))
        ]

    owned = await query_owned_apps(db, UUID(user_id))

    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for entry in shared_kb:
        aid = entry.get("id")
        if not aid:
            continue
        if aid not in merged:
            merged[aid] = entry
            order.append(aid)
    for entry in owned:
        aid = entry["id"]
        if aid not in merged:
            merged[aid] = entry
            order.append(aid)
    return [merged[aid] for aid in order]


# --------------------------------------------------------------------- #
# TASK-BUG-2 — App parameters projection (chat user_input_form OR
# workflow start.variables) → flat ParameterSchema dicts for SPA.
#
# docker exec ncmu-backend probe (2026-05-26) — 5 modes in workspace:
#   completion   → model_config.user_input_form (NESTED: [{"<type>": {...}}])
#   chat         → model_config.user_input_form (NESTED)
#   agent-chat   → model_config.user_input_form (NESTED)
#   workflow     → workflows/draft.graph.nodes[type=start].data.variables (FLAT)
#   advanced-chat→ workflows/draft.graph.nodes[type=start].data.variables (FLAT)
# Mode literal value = .mode field (Boss T2.1=A: explicit dispatch).
# --------------------------------------------------------------------- #

# Dify nested chat-mode type key → SPA ParameterSchema.type字面.
# Anything not in this map is silently skipped (Boss T1.2=A: SPA falls
# back to empty form, future Dify type additions don't break endpoint).
_CHAT_TYPE_MAP: dict[str, str] = {
    "text-input": "text",
    "paragraph": "paragraph",
    "select": "select",
    "number": "number",
}

# Dify workflow start.variables type field — already flat. Keys we accept
# verbatim (SPA contract字面同名). Other types silently skipped.
_WORKFLOW_TYPES: frozenset[str] = frozenset({"text", "paragraph", "select", "number"})

# Mode literals that store parameters in workflows/draft (vs model_config).
_WORKFLOW_DISPATCH_MODES: frozenset[str] = frozenset({"workflow", "advanced-chat"})


def transform_user_input_form(form: Any) -> list[dict[str, Any]]:
    """Unwrap Dify chat-mode `model_config.user_input_form` → flat dicts.

    Source shape (live capture, completion app 40f7c243):
        [{"paragraph": {"label": "Query", "variable": "query",
                        "required": true, "default": ""}}, ...]
    Target shape (SPA ParameterSchema dict):
        [{"variable": "query", "label": "Query", "type": "paragraph",
          "required": true, "options": []}, ...]

    Skip rules (Boss decisions):
      - unknown outer type key (not in _CHAT_TYPE_MAP) → skip entry (T1.2=A)
      - missing variable → skip entry (T1.4=B)
      - missing label → fallback to variable (T1.4=B)
    """
    if not isinstance(form, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in form:
        if not isinstance(entry, dict) or len(entry) != 1:
            continue
        type_key, inner = next(iter(entry.items()))
        mapped_type = _CHAT_TYPE_MAP.get(type_key)
        if mapped_type is None or not isinstance(inner, dict):
            continue
        variable = inner.get("variable")
        if not isinstance(variable, str) or not variable:
            continue
        label_raw = inner.get("label")
        label = label_raw if isinstance(label_raw, str) and label_raw else variable
        options = inner.get("options") if mapped_type == "select" else None
        options_list = [str(o) for o in options] if isinstance(options, list) else []
        out.append({
            "variable": variable,
            "label": label,
            "type": mapped_type,
            "required": bool(inner.get("required") or False),
            "options": options_list,
        })
    return out


def transform_workflow_variables(variables: Any) -> list[dict[str, Any]]:
    """Project Dify workflow start.variables → ParameterSchema dicts.

    Source shape (live capture, workflow app 5e84f3e8):
        [{"variable": "x", "label": "x", "type": "number", "required": true,
          "options": [], "placeholder": "", "default": "", "hint": ""}, ...]
    Target shape: same keys minus placeholder/default/hint.

    Skip rules: type not in _WORKFLOW_TYPES → skip (T1.2=A);
    missing variable → skip (T1.4=B); missing label → fallback to variable.
    """
    if not isinstance(variables, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in variables:
        if not isinstance(entry, dict):
            continue
        var_type = entry.get("type")
        if var_type not in _WORKFLOW_TYPES:
            continue
        variable = entry.get("variable")
        if not isinstance(variable, str) or not variable:
            continue
        label_raw = entry.get("label")
        label = label_raw if isinstance(label_raw, str) and label_raw else variable
        options = entry.get("options") if var_type == "select" else None
        options_list = [str(o) for o in options] if isinstance(options, list) else []
        out.append({
            "variable": variable,
            "label": label,
            "type": var_type,
            "required": bool(entry.get("required") or False),
            "options": options_list,
        })
    return out


def _extract_start_node_variables(draft: dict[str, Any]) -> list[Any]:
    """Walk workflows/draft.graph.nodes for the start node's `.data.variables`."""
    graph = draft.get("graph") or {}
    nodes = graph.get("nodes") or []
    if not isinstance(nodes, list):
        return []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        data = n.get("data") or {}
        if isinstance(data, dict) and data.get("type") in ("start", "Start"):
            vs = data.get("variables")
            return vs if isinstance(vs, list) else []
    return []


async def get_app_parameters(
    *,
    app_id: str,
    http: httpx.AsyncClient,
    cache: DifyConsoleClient,
    settings: Settings,
) -> list[dict[str, Any]]:
    """Dispatch on .mode field → fetch the right Dify endpoint → flat dicts.

    Boss T2.1=A: explicit .mode字面 branch (not implicit dict-non-None).
    Mode literals seen in workspace (docker exec 2026-05-26):
        workflow / advanced-chat → workflows/draft path
        completion / chat / agent-chat → model_config path
    Unknown mode → return [] (safe; SPA `parameters ?? []` tolerates).

    Caller must run user_can_access_app guard FIRST (403 surface).
    Dify 4xx/5xx propagates as 502 (matches existing /apps endpoint).
    """
    detail = await cache.fetch_app_detail(
        app_id=app_id,
        http=http,
        base_url=settings.DIFY_BASE_URL,
        api_key=settings.DIFY_CONSOLE_API_KEY,
        tenant_id=settings.DIFY_TENANT_ID,
    )
    mode = detail.get("mode")
    if mode in _WORKFLOW_DISPATCH_MODES:
        draft = await cache.fetch_workflow_draft(
            app_id=app_id,
            http=http,
            base_url=settings.DIFY_BASE_URL,
            api_key=settings.DIFY_CONSOLE_API_KEY,
            tenant_id=settings.DIFY_TENANT_ID,
        )
        return transform_workflow_variables(_extract_start_node_variables(draft))
    model_config = detail.get("model_config") or {}
    if not isinstance(model_config, dict):
        return []
    return transform_user_input_form(model_config.get("user_input_form"))
