"""DingTalk 通讯录同步编排 — TASK-2DA2S-03.

拉钉钉「软件开发部子树」组织 → 给子树内每个员工建/更新 NCMU user 行并打
``user_tags``（按 ``department_tag_mappings``）。设计 spec
``NCMU-Wiki/sources/phase2/2026-06-01-phase2d-a2-sync-plan.md`` §TASK-2DA2S-03。

Boss 三决策：

* **dept_id 精确映射**：``department_tag_mappings`` (T02) 一部门→多 tag。
* **scope 限定子树**：只同步 ``root_dept_id`` 子树（首批 dogfood = 软件开发部），
  子树外既有 NCMU user 一律不碰（不删不改）。
* **B 策略建账号**：按 ``dingtalk_userid`` 查不到就 INSERT 新 user 行，只写
  ``id``/``dingtalk_userid``/``name``/``dept_path`` —— **User 模型无 password/email
  列**，建账号零凭据；登录半（2D-A2-登录）按 ``dingtalk_userid`` 匹配既有。

幂等：``user_tags`` 是 replace-all（非 append），已建 user 走 update 分支不重复
建，重复跑结果一致。

⚠️ **根部门成员（T01 主审 catch）**：``client.list_departments`` 只返回**子部门**，
不含 ``root_dept_id`` 自身。故拉成员时遍历的 dept 集合必须 = 子部门 ∪ {root}，
否则直属软件开发部根部门的员工（首批 dogfood）会被漏同步（见 :func:`sync_contacts`
注释 + AC#4b 反例测）。
"""
from __future__ import annotations

import uuid
from typing import Any

import httpx
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.config import Settings
from ncmu_backend.db.models import DepartmentTagMapping, User, UserTag
from ncmu_backend.dingtalk.client import (
    get_access_token,
    list_department_users,
    list_departments,
)


class SyncResult(BaseModel):
    """单次同步的计数摘要（admin POST /sync 的 JSON 响应体）。"""

    root_dept_id: int
    departments: int   # 实际遍历的部门数（子部门 ∪ {root}）
    users_seen: int    # 子树内去重后的钉钉员工数
    users_created: int  # 本次 INSERT 的新 NCMU user 行数
    users_updated: int  # 本次命中既有 dingtalk_userid 走 update 的数
    tags_written: int  # 本次写入的 user_tags 行总数（replace-all 后）


def _build_dept_path(
    dept_ids: set[int], dept_meta: dict[int, dict[str, Any]]
) -> str | None:
    """为 user 构「/A/B/C」部门名链（取其最深的**已知命名**部门）。

    沿 ``parent_id`` 上溯，逐级 prepend 名字，止于 ``dept_meta`` 之外的祖先
    （即子树 root 或更高 —— 这些层 ``list_departments`` 不返回名字）。

    **限制（client.py 禁改 / T01 冻结）**：``list_departments`` 不返回子树 root
    自身的名字，故仅直属 ``root_dept_id``（不在任何命名子部门）的成员得 ``None``；
    在命名子部门内的成员得其名链。多部门归属时取最深链（同深以 dept_id 升序定序，
    保证幂等确定性）。
    """
    best: str | None = None
    best_depth = -1
    for d in sorted(dept_ids):
        if d not in dept_meta:
            continue
        chain: list[str] = []
        cur: int | None = d
        seen: set[int] = set()
        while cur is not None and cur in dept_meta and cur not in seen:
            seen.add(cur)
            name = dept_meta[cur].get("name")
            if name:
                chain.append(name)
            cur = dept_meta[cur].get("parent_id")
        if not chain:
            continue
        chain.reverse()
        if len(chain) > best_depth:
            best_depth = len(chain)
            best = "/" + "/".join(chain)
    return best


async def sync_contacts(
    db: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    root_dept_id: int,
) -> SyncResult:
    """编排同步 ``root_dept_id`` 子树通讯录 → NCMU user + user_tags。

    步骤：token → 拉子树部门 → 预读 mapping → 逐部门拉成员（按 dingtalk_userid
    聚合多部门归属）→ 每 user 建/更新 + replace-all user_tags → commit。
    """
    token = await get_access_token(http, settings)
    sub_depts = await list_departments(http, settings, token, root_dept_id=root_dept_id)

    # dept_id -> {name, parent_id}（仅子部门；root 自身不在 list_departments 返回里）。
    dept_meta: dict[int, dict[str, Any]] = {
        d["dept_id"]: {"name": d.get("name"), "parent_id": d.get("parent_id")}
        for d in sub_depts
        if d.get("dept_id") is not None
    }

    # ⚠️ 根部门成员 fix（T01 主审 catch）：遍历 dept 集合 = 子部门 ∪ {root}。
    dept_ids: list[int] = list(dept_meta.keys())
    if root_dept_id not in dept_meta:
        dept_ids.append(root_dept_id)

    # 预读 department_tag_mappings 全表 → dept_id -> set[tag_id]（一次性）。
    mapping_rows = (await db.execute(select(DepartmentTagMapping))).scalars().all()
    dept_tags: dict[int, set[uuid.UUID]] = {}
    for m in mapping_rows:
        dept_tags.setdefault(m.dept_id, set()).add(m.tag_id)

    # 按 dingtalk_userid 聚合：所属（子树内）部门集合 + 展示名。同一 user 可出现
    # 在多个部门（user/list 按部门返回），故聚合后每 user 只处理一次（幂等关键）。
    user_depts: dict[str, set[int]] = {}
    user_name: dict[str, str] = {}
    for dept_id in dept_ids:
        members = await list_department_users(http, settings, token, dept_id)
        for member in members:
            ding_uid = member.get("userid")
            if not ding_uid:
                continue
            user_depts.setdefault(ding_uid, set()).add(dept_id)
            if member.get("name") is not None:
                user_name[ding_uid] = member["name"]

    created = 0
    updated = 0
    tags_written = 0

    for ding_uid, depts_of_user in user_depts.items():
        # B 策略：按 dingtalk_userid 查既有 → 命中 update / 否则 INSERT 建行。
        existing = (
            await db.execute(select(User).where(User.dingtalk_userid == ding_uid))
        ).scalar_one_or_none()

        name = user_name.get(ding_uid) or ding_uid
        dept_path = _build_dept_path(depts_of_user, dept_meta)

        if existing is None:
            user = User(
                id=uuid.uuid4(),  # PK 无 server default → 建行必须显式给
                dingtalk_userid=ding_uid,
                name=name,
                dept_path=dept_path,
                # is_active server_default true；模型无 password/email 列 → 零凭据。
            )
            db.add(user)
            await db.flush()  # 物化 user.id 供 user_tags FK + 保 INSERT 顺序
            created += 1
        else:
            existing.name = name
            existing.dept_path = dept_path
            user = existing
            updated += 1

        # replace-all：user_tags = ∪ 该 user 所有「子树内且有 mapping」部门的 tag。
        tag_ids: set[uuid.UUID] = set()
        for d in depts_of_user:
            tag_ids |= dept_tags.get(d, set())
        await db.execute(delete(UserTag).where(UserTag.user_id == user.id))
        for tid in tag_ids:
            db.add(UserTag(user_id=user.id, tag_id=tid))
        tags_written += len(tag_ids)

    await db.commit()

    return SyncResult(
        root_dept_id=root_dept_id,
        departments=len(dept_ids),
        users_seen=len(user_depts),
        users_created=created,
        users_updated=updated,
        tags_written=tags_written,
    )
