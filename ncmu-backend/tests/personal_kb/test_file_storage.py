"""Unit tests for personal_kb/file_storage.py — real filesystem, no mocks.

Per `feedback_tdd_mock_vs_real_api` (mock-vs-real三支柱), the storage
backend is exercised against a real ``tmp_path`` so the byte-level
round-trip (save → open → bytes-equal) is actually verified.
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import AsyncIterator

import pytest

from ncmu_backend.personal_kb.file_storage import (
    LocalFsBackend,
    StorageBackend,
    StorageRef,
)


async def _chunks_of(blob: bytes, chunk_size: int = 1024) -> AsyncIterator[bytes]:
    for i in range(0, len(blob), chunk_size):
        yield blob[i:i + chunk_size]


async def _drain(it: AsyncIterator[bytes]) -> bytes:
    out = bytearray()
    async for chunk in it:
        out.extend(chunk)
    return bytes(out)


# --------------------------------------------------------------------- #
# Save / open / delete — happy path
# --------------------------------------------------------------------- #
async def test_save_then_open_round_trips_bytes_exactly(tmp_path: Path) -> None:
    backend = LocalFsBackend(tmp_path)
    payload = b"\x00\x01hello world\n" * 100  # ~1.3 KB, multi-chunk
    app_id = uuid.uuid4()
    file_id = uuid.uuid4()

    ref = await backend.save(app_id, file_id, "doc.txt", _chunks_of(payload))

    assert isinstance(ref, StorageRef)
    assert ref.backend == "localfs"
    assert ref.path.endswith(f"{app_id}/{file_id}_doc.txt")
    assert Path(ref.path).exists()

    streamed = await _drain(backend.open(ref))
    assert streamed == payload


async def test_save_creates_application_subdirectory(tmp_path: Path) -> None:
    backend = LocalFsBackend(tmp_path)
    app_id = uuid.uuid4()
    file_id = uuid.uuid4()
    await backend.save(app_id, file_id, "a.txt", _chunks_of(b"x"))
    assert (tmp_path / str(app_id)).is_dir()


async def test_save_strips_path_components_from_filename(tmp_path: Path) -> None:
    """Defensive: a malicious uploader can't escape the storage root."""
    backend = LocalFsBackend(tmp_path)
    app_id = uuid.uuid4()
    file_id = uuid.uuid4()
    ref = await backend.save(
        app_id, file_id, "../../etc/passwd", _chunks_of(b"x")
    )
    # Original filename collapsed to bare basename inside the app dir.
    assert Path(ref.path).parent == tmp_path / str(app_id)
    assert Path(ref.path).name == f"{file_id}_passwd"


async def test_open_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    backend = LocalFsBackend(tmp_path)
    ghost = StorageRef(backend="localfs", path=str(tmp_path / "nope.txt"))
    with pytest.raises(FileNotFoundError):
        backend.open(ghost)


async def test_delete_removes_blob_idempotent(tmp_path: Path) -> None:
    backend = LocalFsBackend(tmp_path)
    app_id = uuid.uuid4()
    file_id = uuid.uuid4()
    ref = await backend.save(app_id, file_id, "x.txt", _chunks_of(b"x"))
    assert Path(ref.path).exists()

    await backend.delete(ref)
    assert not Path(ref.path).exists()

    # Second delete is a no-op — never raises.
    await backend.delete(ref)


# --------------------------------------------------------------------- #
# Concurrency — two save()s on disjoint paths must not serialise
# --------------------------------------------------------------------- #
async def test_concurrent_saves_do_not_block_each_other(tmp_path: Path) -> None:
    backend = LocalFsBackend(tmp_path)
    app_id = uuid.uuid4()

    async def _one(name: str) -> StorageRef:
        return await backend.save(
            app_id, uuid.uuid4(), name, _chunks_of(b"payload-" + name.encode())
        )

    refs = await asyncio.gather(*(_one(f"f{i}.txt") for i in range(4)))
    paths = [Path(r.path) for r in refs]
    assert all(p.exists() for p in paths)
    # All distinct — no lock collision.
    assert len({p.name for p in paths}) == 4


# --------------------------------------------------------------------- #
# Protocol conformance — LocalFsBackend satisfies StorageBackend
# --------------------------------------------------------------------- #
def test_localfs_backend_satisfies_storage_backend_protocol(tmp_path: Path) -> None:
    backend = LocalFsBackend(tmp_path)
    assert isinstance(backend, StorageBackend)
