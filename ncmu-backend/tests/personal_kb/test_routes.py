"""Integration tests for personal_kb/routes.py (employee surface).

Uses the shared ``app_client`` fixture from ``tests/conftest.py`` so
every HTTP call rides the FastAPI app with a real async DB session.
Storage is redirected at ``tmp_path`` per-test via ``app.dependency_overrides``.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest_asyncio

from ncmu_backend.personal_kb.file_storage import LocalFsBackend


# Fixed dev-seed UUIDs (admin = 张三 = a0...0001 maps to NCMU_ADMIN_USER_IDS
# default; 李四 = a0...0002 is a plain employee).
ADMIN_USER_ID = "a0000001-0000-4000-8000-000000000001"
USER_LISI_ID = "a0000001-0000-4000-8000-000000000002"


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


# --------------------------------------------------------------------- #
# AC#1a — POST happy path (multipart upload)
# --------------------------------------------------------------------- #
async def test_create_application_returns_201_with_pending_status(
    app_client: httpx.AsyncClient, jwt_secret: str, storage_root: Path
) -> None:
    token = _jwt_for(USER_LISI_ID, "李四", jwt_secret)

    files = [
        ("files", ("hr.txt", b"hello world", "text/plain")),
        ("files", ("hr.pdf", b"%PDF-1.4 stub", "application/pdf")),
    ]
    data = {"kb_name_suggested": "HR 手册", "description": "请协助建库"}

    r = await app_client.post(
        "/api/v1/ncmu/personal-kb/applications",
        files=files,
        data=data,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending"
    # UUID-shaped id.
    uuid.UUID(body["id"])
    assert body["user_id"] == USER_LISI_ID
    assert body["kb_name_suggested"] == "HR 手册"

    # On-disk: 2 files under <storage_root>/<application_id>/
    app_dir = storage_root / body["id"]
    assert app_dir.is_dir()
    blobs = list(app_dir.iterdir())
    assert len(blobs) == 2
    # Filenames carry the original name.
    blob_basenames = {p.name for p in blobs}
    assert any(name.endswith("_hr.txt") for name in blob_basenames)
    assert any(name.endswith("_hr.pdf") for name in blob_basenames)


# --------------------------------------------------------------------- #
# AC#4 — Q2-B validation
# --------------------------------------------------------------------- #
async def test_post_rejects_more_than_10_files(
    app_client: httpx.AsyncClient, jwt_secret: str, storage_root: Path
) -> None:
    token = _jwt_for(USER_LISI_ID, "李四", jwt_secret)
    files = [
        ("files", (f"f{i}.txt", b"x", "text/plain")) for i in range(11)
    ]
    r = await app_client.post(
        "/api/v1/ncmu/personal-kb/applications",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422
    assert "最多上传 10 个文件" in r.json()["detail"]


async def test_post_rejects_oversize_file(
    app_client: httpx.AsyncClient, jwt_secret: str, storage_root: Path,
    monkeypatch,
) -> None:
    """Set MAX_FILE_SIZE_BYTES to a tiny value so we can prove the check
    without shoving 50 MB through the test transport."""
    monkeypatch.setattr(
        "ncmu_backend.personal_kb.routes.MAX_FILE_SIZE_BYTES", 16
    )
    token = _jwt_for(USER_LISI_ID, "李四", jwt_secret)
    files = [("files", ("big.txt", b"A" * 100, "text/plain"))]
    r = await app_client.post(
        "/api/v1/ncmu/personal-kb/applications",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422
    assert "单文件不能超过" in r.json()["detail"]


async def test_post_rejects_unknown_extension(
    app_client: httpx.AsyncClient, jwt_secret: str, storage_root: Path
) -> None:
    token = _jwt_for(USER_LISI_ID, "李四", jwt_secret)
    files = [("files", ("malware.exe", b"MZ", "application/octet-stream"))]
    r = await app_client.post(
        "/api/v1/ncmu/personal-kb/applications",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422
    assert "不支持的文件格式 .exe" in r.json()["detail"]


# --------------------------------------------------------------------- #
# AC#C2 — partial-upload failure must sweep prior-staged blobs
#         (spec §4.1 cleanup 字面 / INDEP fresh-eyes 2nd-round catch)
# --------------------------------------------------------------------- #
async def test_post_partial_failure_cleans_prior_blobs(
    app_client: httpx.AsyncClient, jwt_secret: str, storage_root: Path,
    monkeypatch,
) -> None:
    """Reproduce the orphan-blob scenario INDEP flagged: when the 2nd
    file trips the MAX_FILE_SIZE_BYTES guard *after* the 1st has already
    landed on disk, the cleanup-on-failure path must delete every
    prior-staged blob so DB rollback ≡ filesystem rollback.
    """
    # Shrink the per-file size limit so a tiny payload trips it,
    # but only after the first (valid_small) file has been written.
    monkeypatch.setattr(
        "ncmu_backend.personal_kb.routes.MAX_FILE_SIZE_BYTES", 16
    )
    token = _jwt_for(USER_LISI_ID, "李四", jwt_secret)
    files = [
        ("files", ("ok.txt", b"x" * 5, "text/plain")),     # within 16 B
        ("files", ("big.txt", b"A" * 100, "text/plain")),  # exceeds 16 B
    ]
    r = await app_client.post(
        "/api/v1/ncmu/personal-kb/applications",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422
    assert "单文件不能超过" in r.json()["detail"]

    # On-disk: the application_id directory MUST be empty (or absent).
    # Any leftover bytes here would be an orphan — the DB row got
    # rolled back, so the blob has no row pointing at it.
    leftovers: list[Path] = []
    for app_dir in storage_root.iterdir():
        if app_dir.is_dir():
            leftovers.extend(p for p in app_dir.iterdir())
    assert leftovers == [], f"orphan blobs after partial failure: {leftovers}"


# --------------------------------------------------------------------- #
# AC#1b — GET list (status filter + ownership scoping)
# --------------------------------------------------------------------- #
async def test_get_lists_only_own_applications(
    app_client: httpx.AsyncClient, jwt_secret: str, storage_root: Path
) -> None:
    lisi = _jwt_for(USER_LISI_ID, "李四", jwt_secret)
    files = [("files", ("a.txt", b"x", "text/plain"))]

    for _ in range(2):
        await app_client.post(
            "/api/v1/ncmu/personal-kb/applications",
            files=files,
            headers={"Authorization": f"Bearer {lisi}"},
        )

    r = await app_client.get(
        "/api/v1/ncmu/personal-kb/applications",
        headers={"Authorization": f"Bearer {lisi}"},
    )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    for row in rows:
        assert row["user_id"] == USER_LISI_ID
        assert row["status"] == "pending"
        for key in ("id", "status", "kb_name_suggested", "created_at"):
            assert key in row


async def test_get_filters_by_status(
    app_client: httpx.AsyncClient, jwt_secret: str, storage_root: Path
) -> None:
    lisi = _jwt_for(USER_LISI_ID, "李四", jwt_secret)
    files = [("files", ("a.txt", b"x", "text/plain"))]
    await app_client.post(
        "/api/v1/ncmu/personal-kb/applications",
        files=files,
        headers={"Authorization": f"Bearer {lisi}"},
    )
    r = await app_client.get(
        "/api/v1/ncmu/personal-kb/applications?status=pending",
        headers={"Authorization": f"Bearer {lisi}"},
    )
    assert r.status_code == 200
    assert all(row["status"] == "pending" for row in r.json())

    r2 = await app_client.get(
        "/api/v1/ncmu/personal-kb/applications?status=done",
        headers={"Authorization": f"Bearer {lisi}"},
    )
    assert r2.status_code == 200
    assert r2.json() == []


# --------------------------------------------------------------------- #
# AC#1c — DELETE withdraw
# --------------------------------------------------------------------- #
async def test_delete_pending_withdraws_204(
    app_client: httpx.AsyncClient, jwt_secret: str, storage_root: Path
) -> None:
    lisi = _jwt_for(USER_LISI_ID, "李四", jwt_secret)
    create = await app_client.post(
        "/api/v1/ncmu/personal-kb/applications",
        files=[("files", ("a.txt", b"x", "text/plain"))],
        headers={"Authorization": f"Bearer {lisi}"},
    )
    app_id = create.json()["id"]
    r = await app_client.delete(
        f"/api/v1/ncmu/personal-kb/applications/{app_id}",
        headers={"Authorization": f"Bearer {lisi}"},
    )
    assert r.status_code == 204

    # GET list shows cancelled now.
    listed = await app_client.get(
        "/api/v1/ncmu/personal-kb/applications?status=cancelled",
        headers={"Authorization": f"Bearer {lisi}"},
    )
    statuses = {row["status"] for row in listed.json()}
    assert statuses == {"cancelled"}


async def test_delete_others_application_returns_404(
    app_client: httpx.AsyncClient, jwt_secret: str, storage_root: Path
) -> None:
    lisi = _jwt_for(USER_LISI_ID, "李四", jwt_secret)
    create = await app_client.post(
        "/api/v1/ncmu/personal-kb/applications",
        files=[("files", ("a.txt", b"x", "text/plain"))],
        headers={"Authorization": f"Bearer {lisi}"},
    )
    app_id = create.json()["id"]

    # 王五 = a0...0003 attempts to delete 李四's app.
    wangwu = _jwt_for("a0000001-0000-4000-8000-000000000003", "王五", jwt_secret)
    r = await app_client.delete(
        f"/api/v1/ncmu/personal-kb/applications/{app_id}",
        headers={"Authorization": f"Bearer {wangwu}"},
    )
    assert r.status_code == 404  # NOT 403 — anti-enumeration


async def test_delete_missing_application_returns_404(
    app_client: httpx.AsyncClient, jwt_secret: str, storage_root: Path
) -> None:
    lisi = _jwt_for(USER_LISI_ID, "李四", jwt_secret)
    ghost = str(uuid.uuid4())
    r = await app_client.delete(
        f"/api/v1/ncmu/personal-kb/applications/{ghost}",
        headers={"Authorization": f"Bearer {lisi}"},
    )
    assert r.status_code == 404


# --------------------------------------------------------------------- #
# Auth — endpoint requires Authorization
# --------------------------------------------------------------------- #
async def test_post_without_token_returns_401(
    app_client: httpx.AsyncClient, storage_root: Path
) -> None:
    r = await app_client.post(
        "/api/v1/ncmu/personal-kb/applications",
        files=[("files", ("a.txt", b"x", "text/plain"))],
    )
    assert r.status_code == 401
