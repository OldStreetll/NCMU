"""Integration tests for personal_kb/admin_routes.py (admin surface).

Covers AC#2 a-f: list / detail+files / download / claim / dispatch / reject.
Permission gate (require_admin) is verified twice — once per non-admin
endpoint that touches DB, plus a wildcard on /claim.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest_asyncio

from ncmu_backend.personal_kb.file_storage import LocalFsBackend


ADMIN_USER_ID = "a0000001-0000-4000-8000-000000000001"  # 张三 (admin per seed)
USER_LISI_ID = "a0000001-0000-4000-8000-000000000002"
USER_WANGWU_ID = "a0000001-0000-4000-8000-000000000003"


def _jwt_for(user_id: str, name: str, secret: str) -> str:
    from ncmu_backend.auth.jwt_utils import sign_jwt
    token, _ = sign_jwt(user_id=user_id, name=name, secret=secret)
    return token


@pytest_asyncio.fixture
async def storage_root(tmp_path: Path, app_client):
    """Point ``get_storage_backend`` at a tmp dir for this test."""
    from ncmu_backend.main import app
    from ncmu_backend.personal_kb.routes import get_storage_backend

    backend = LocalFsBackend(tmp_path)
    app.dependency_overrides[get_storage_backend] = lambda: backend
    yield tmp_path
    app.dependency_overrides.pop(get_storage_backend, None)


async def _create_pending(
    client: httpx.AsyncClient, token: str, content: bytes = b"hello"
) -> dict:
    r = await client.post(
        "/api/v1/ncmu/personal-kb/applications",
        files=[("files", ("doc.txt", content, "text/plain"))],
        data={"kb_name_suggested": "HR"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------- #
# AC#2a — admin list
# --------------------------------------------------------------------- #
async def test_admin_list_returns_all_users_apps(
    app_client: httpx.AsyncClient, jwt_secret: str, storage_root: Path
) -> None:
    admin = _jwt_for(ADMIN_USER_ID, "张三", jwt_secret)
    lisi = _jwt_for(USER_LISI_ID, "李四", jwt_secret)
    wangwu = _jwt_for(USER_WANGWU_ID, "王五", jwt_secret)
    await _create_pending(app_client, lisi)
    await _create_pending(app_client, wangwu)

    r = await app_client.get(
        "/api/v1/ncmu/admin/personal-kb/applications",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    user_ids = {row["user_id"] for row in rows}
    assert user_ids == {USER_LISI_ID, USER_WANGWU_ID}


async def test_admin_list_rejects_non_admin_403(
    app_client: httpx.AsyncClient, jwt_secret: str, storage_root: Path
) -> None:
    lisi = _jwt_for(USER_LISI_ID, "李四", jwt_secret)
    r = await app_client.get(
        "/api/v1/ncmu/admin/personal-kb/applications",
        headers={"Authorization": f"Bearer {lisi}"},
    )
    assert r.status_code == 403


# --------------------------------------------------------------------- #
# AC#2b — admin detail (includes files)
# --------------------------------------------------------------------- #
async def test_admin_detail_includes_files(
    app_client: httpx.AsyncClient, jwt_secret: str, storage_root: Path
) -> None:
    admin = _jwt_for(ADMIN_USER_ID, "张三", jwt_secret)
    lisi = _jwt_for(USER_LISI_ID, "李四", jwt_secret)
    created = await _create_pending(app_client, lisi, content=b"x" * 42)
    app_id = created["id"]

    r = await app_client.get(
        f"/api/v1/ncmu/admin/personal-kb/applications/{app_id}",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == app_id
    assert "files" in body
    assert len(body["files"]) == 1
    file_row = body["files"][0]
    assert file_row["original_filename"] == "doc.txt"
    assert file_row["file_size_bytes"] == 42


async def test_admin_detail_404_for_missing(
    app_client: httpx.AsyncClient, jwt_secret: str, storage_root: Path
) -> None:
    admin = _jwt_for(ADMIN_USER_ID, "张三", jwt_secret)
    r = await app_client.get(
        f"/api/v1/ncmu/admin/personal-kb/applications/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 404


# --------------------------------------------------------------------- #
# AC#2c — admin download
# --------------------------------------------------------------------- #
async def test_admin_download_streams_original_bytes(
    app_client: httpx.AsyncClient, jwt_secret: str, storage_root: Path
) -> None:
    admin = _jwt_for(ADMIN_USER_ID, "张三", jwt_secret)
    lisi = _jwt_for(USER_LISI_ID, "李四", jwt_secret)
    payload = b"the quick brown fox" * 50
    created = await _create_pending(app_client, lisi, content=payload)

    # Look up file_id via the admin detail endpoint.
    detail = await app_client.get(
        f"/api/v1/ncmu/admin/personal-kb/applications/{created['id']}",
        headers={"Authorization": f"Bearer {admin}"},
    )
    file_id = detail.json()["files"][0]["id"]

    r = await app_client.get(
        f"/api/v1/ncmu/admin/personal-kb/applications/{created['id']}/files/{file_id}/download",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 200
    assert r.content == payload
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert "doc.txt" in cd


# --------------------------------------------------------------------- #
# AC#2d — admin claim (pending → in_progress)
# --------------------------------------------------------------------- #
async def test_admin_claim_transitions_to_in_progress(
    app_client: httpx.AsyncClient, jwt_secret: str, storage_root: Path
) -> None:
    admin = _jwt_for(ADMIN_USER_ID, "张三", jwt_secret)
    lisi = _jwt_for(USER_LISI_ID, "李四", jwt_secret)
    created = await _create_pending(app_client, lisi)

    r = await app_client.post(
        f"/api/v1/ncmu/admin/personal-kb/applications/{created['id']}/claim",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "in_progress"
    assert body["admin_processed_by"] == ADMIN_USER_ID
    assert body["admin_processed_at"] is not None


async def test_admin_claim_409_for_already_in_progress(
    app_client: httpx.AsyncClient, jwt_secret: str, storage_root: Path
) -> None:
    admin = _jwt_for(ADMIN_USER_ID, "张三", jwt_secret)
    lisi = _jwt_for(USER_LISI_ID, "李四", jwt_secret)
    created = await _create_pending(app_client, lisi)
    # First claim succeeds.
    await app_client.post(
        f"/api/v1/ncmu/admin/personal-kb/applications/{created['id']}/claim",
        headers={"Authorization": f"Bearer {admin}"},
    )
    # Second claim → 409.
    r = await app_client.post(
        f"/api/v1/ncmu/admin/personal-kb/applications/{created['id']}/claim",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 409
    assert "认领" in r.json()["detail"]


# --------------------------------------------------------------------- #
# AC#2e — admin dispatch (in_progress → done; 4 SQL atomic)
# --------------------------------------------------------------------- #
async def test_admin_dispatch_marks_done_and_writes_external_rows(
    app_client: httpx.AsyncClient, jwt_secret: str, storage_root: Path,
    async_db,
) -> None:
    from sqlalchemy import text
    admin = _jwt_for(ADMIN_USER_ID, "张三", jwt_secret)
    lisi = _jwt_for(USER_LISI_ID, "李四", jwt_secret)
    created = await _create_pending(app_client, lisi)
    app_id = created["id"]

    # claim first.
    await app_client.post(
        f"/api/v1/ncmu/admin/personal-kb/applications/{app_id}/claim",
        headers={"Authorization": f"Bearer {admin}"},
    )

    # dispatch.
    name = f"hr-handbook-{uuid.uuid4().hex[:6]}"
    dify_app_id = f"app-personal-{uuid.uuid4().hex[:6]}"
    r = await app_client.post(
        f"/api/v1/ncmu/admin/personal-kb/applications/{app_id}/dispatch",
        json={
            "dataset_id": "ds-fastgpt-001",
            "dify_app_id": dify_app_id,
            "kb_name_final": name,
        },
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "done"
    assert body["dify_app_id"] == dify_app_id

    # Cross-table observable effects.
    kb_row = (await async_db.execute(
        text("SELECT fastgpt_dataset_id FROM dify_external_kb_configs "
             "WHERE external_kb_name=:n"),
        {"n": name},
    )).scalar_one()
    assert kb_row == "ds-fastgpt-001"
    owner_row = (await async_db.execute(
        text("SELECT visibility FROM app_owners WHERE app_id=:a"),
        {"a": dify_app_id},
    )).scalar_one()
    assert owner_row == "owner_only"


async def test_admin_dispatch_409_if_not_in_progress(
    app_client: httpx.AsyncClient, jwt_secret: str, storage_root: Path
) -> None:
    admin = _jwt_for(ADMIN_USER_ID, "张三", jwt_secret)
    lisi = _jwt_for(USER_LISI_ID, "李四", jwt_secret)
    created = await _create_pending(app_client, lisi)
    # Skip claim — dispatch attempted on pending status.
    r = await app_client.post(
        f"/api/v1/ncmu/admin/personal-kb/applications/{created['id']}/dispatch",
        json={
            "dataset_id": "ds-1",
            "dify_app_id": "app-x",
            "kb_name_final": f"x-{uuid.uuid4().hex[:6]}",
        },
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 409
    assert "认领" in r.json()["detail"]


# --------------------------------------------------------------------- #
# AC#2f — admin reject
# --------------------------------------------------------------------- #
async def test_admin_reject_pending_records_reason(
    app_client: httpx.AsyncClient, jwt_secret: str, storage_root: Path
) -> None:
    admin = _jwt_for(ADMIN_USER_ID, "张三", jwt_secret)
    lisi = _jwt_for(USER_LISI_ID, "李四", jwt_secret)
    created = await _create_pending(app_client, lisi)
    r = await app_client.post(
        f"/api/v1/ncmu/admin/personal-kb/applications/{created['id']}/reject",
        json={"rejection_reason": "材料不全"},
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "rejected"
    assert body["rejection_reason"] == "材料不全"


async def test_admin_reject_409_for_terminal_state(
    app_client: httpx.AsyncClient, jwt_secret: str, storage_root: Path
) -> None:
    admin = _jwt_for(ADMIN_USER_ID, "张三", jwt_secret)
    lisi = _jwt_for(USER_LISI_ID, "李四", jwt_secret)
    created = await _create_pending(app_client, lisi)
    # 1st reject puts it into terminal 'rejected'.
    await app_client.post(
        f"/api/v1/ncmu/admin/personal-kb/applications/{created['id']}/reject",
        json={"rejection_reason": "first"},
        headers={"Authorization": f"Bearer {admin}"},
    )
    # 2nd reject → 409.
    r = await app_client.post(
        f"/api/v1/ncmu/admin/personal-kb/applications/{created['id']}/reject",
        json={"rejection_reason": "second"},
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 409


# --------------------------------------------------------------------- #
# Permission gate
# --------------------------------------------------------------------- #
async def test_non_admin_claim_403(
    app_client: httpx.AsyncClient, jwt_secret: str, storage_root: Path
) -> None:
    lisi = _jwt_for(USER_LISI_ID, "李四", jwt_secret)
    created = await _create_pending(app_client, lisi)
    r = await app_client.post(
        f"/api/v1/ncmu/admin/personal-kb/applications/{created['id']}/claim",
        headers={"Authorization": f"Bearer {lisi}"},
    )
    assert r.status_code == 403


# --------------------------------------------------------------------- #
# REWORK-PC2-FIX-C1 — DataError catch on dispatch returns 422 instead of 500
# --------------------------------------------------------------------- #
async def test_admin_dispatch_kb_name_final_exceeds_db_column_returns_422(
    app_client: httpx.AsyncClient, jwt_secret: str, storage_root: Path,
    async_db,
) -> None:
    """Defense-in-depth: a future cross-stack drift where the DB column is
    tighter than the Pydantic schema must surface as a clean 422 instead of
    leaking psycopg StringDataRightTruncation through as 500.

    Setup uses real PG (守 mock-vs-real 三支柱): on the ephemeral test DB,
    temporarily narrow external_kb_name back to VARCHAR(64) (post-alembic-0008
    rollback simulation). A 65-char ``kb_name_final`` then passes Pydantic
    (max_length=200) and hits the DB column boundary, raising a real
    DBAPIError with PG SQLSTATE 22001 that the ``except DBAPIError`` branch
    in admin_routes.py must translate to 422 with the Chinese detail copy.
    (asyncpg dialect does not classify StringDataRightTruncationError as
    sqlalchemy.exc.DataError, hence the broader DBAPIError + SQLSTATE gate.)
    """
    from sqlalchemy import text

    admin = _jwt_for(ADMIN_USER_ID, "张三", jwt_secret)
    lisi = _jwt_for(USER_LISI_ID, "李四", jwt_secret)
    created = await _create_pending(app_client, lisi)
    app_id = created["id"]

    # claim (real state machine + real DB write).
    await app_client.post(
        f"/api/v1/ncmu/admin/personal-kb/applications/{app_id}/claim",
        headers={"Authorization": f"Bearer {admin}"},
    )

    # Narrow the ephemeral test DB column back to VARCHAR(64) — table is
    # empty so the cast is a no-op data-wise. The ephemeral DB is torn
    # down after this test so no other test is affected.
    await async_db.execute(
        text(
            "ALTER TABLE dify_external_kb_configs "
            "ALTER COLUMN external_kb_name TYPE VARCHAR(64)"
        )
    )
    await async_db.commit()

    oversized = "A" * 65  # passes Pydantic max_length=200, busts VARCHAR(64)
    r = await app_client.post(
        f"/api/v1/ncmu/admin/personal-kb/applications/{app_id}/dispatch",
        json={
            "dataset_id": "ds-fastgpt-c1",
            "dify_app_id": f"app-personal-{uuid.uuid4().hex[:6]}",
            "kb_name_final": oversized,
        },
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 422, r.text
    assert "长度" in r.json()["detail"]
