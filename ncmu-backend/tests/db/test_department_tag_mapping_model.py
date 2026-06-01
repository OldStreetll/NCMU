"""TASK-2DA2S-02 — DepartmentTagMapping ORM + schema smoke tests.

AC#2 + AC#3 coverage (db_session fixture runs `alembic upgrade head`
against an ephemeral pg-ncmu, so migration 0011 must have created
``department_tag_mappings`` before these run):

  (a) DepartmentTagMapping class imports + __tablename__ correct
  (b) ORM insert 1 row + read back dept_id / tag_id 字面一致
  (c) composite PK (dept_id, tag_id) rejects a duplicate pair
  (d) same dept_id can map to >1 tag (composite PK 非单列唯一)
  (e) FK tag_id → tags.id ON DELETE CASCADE: 删 tag 级联删映射
  (f) tag_id 必须指向存在的 tag (FK 拒绝悬空引用)
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


def _seed_tag(db_session, name: str) -> str:
    """Insert one tags row, return its UUID (as str)."""
    return str(
        db_session.execute(
            text("INSERT INTO tags (name) VALUES (:n) RETURNING id"),
            {"n": name},
        ).scalar_one()
    )


def test_department_tag_mapping_class_import_and_tablename():
    from ncmu_backend.db.models import DepartmentTagMapping

    assert DepartmentTagMapping.__tablename__ == "department_tag_mappings"


def test_insert_and_readback(db_session):
    """AC#3: model 可 import / INSERT / SELECT，字段字面一致。"""
    from ncmu_backend.db.models import DepartmentTagMapping

    tag_id = _seed_tag(db_session, "软件开发部")
    db_session.commit()

    row = DepartmentTagMapping(dept_id=123456789, tag_id=uuid.UUID(tag_id))
    db_session.add(row)
    db_session.commit()

    fetched = db_session.get(DepartmentTagMapping, (123456789, uuid.UUID(tag_id)))
    assert fetched is not None
    assert fetched.dept_id == 123456789
    assert str(fetched.tag_id) == tag_id
    # created_at server_default now() populated on insert
    assert fetched.created_at is not None


def test_dept_id_accepts_bigint(db_session):
    """dept_id 为 BigInteger — 钉钉部门 id 可能超 int32 范围。"""
    from ncmu_backend.db.models import DepartmentTagMapping

    tag_id = _seed_tag(db_session, "大 id 部门")
    db_session.commit()

    big = 9_000_000_000  # > 2^31, fits BigInteger
    db_session.add(DepartmentTagMapping(dept_id=big, tag_id=uuid.UUID(tag_id)))
    db_session.commit()

    fetched = db_session.get(DepartmentTagMapping, (big, uuid.UUID(tag_id)))
    assert fetched is not None and fetched.dept_id == big


def test_composite_pk_rejects_duplicate_pair(db_session):
    """AC#2: 同 (dept_id, tag_id) 复合 PK 防重 → 第二次插入 IntegrityError。"""
    tag_id = _seed_tag(db_session, "重复对测试")
    db_session.commit()

    db_session.execute(
        text(
            "INSERT INTO department_tag_mappings (dept_id, tag_id) "
            "VALUES (:d, CAST(:t AS uuid))"
        ),
        {"d": 555, "t": tag_id},
    )
    db_session.commit()

    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "INSERT INTO department_tag_mappings (dept_id, tag_id) "
                "VALUES (:d, CAST(:t AS uuid))"
            ),
            {"d": 555, "t": tag_id},
        )
        db_session.flush()


def test_one_dept_maps_to_multiple_tags(db_session):
    """复合 PK 非单列唯一：同一 dept_id 可绑多个 tag（一部门多标签）。"""
    tag_a = _seed_tag(db_session, "标签A")
    tag_b = _seed_tag(db_session, "标签B")
    db_session.commit()

    for t in (tag_a, tag_b):
        db_session.execute(
            text(
                "INSERT INTO department_tag_mappings (dept_id, tag_id) "
                "VALUES (:d, CAST(:t AS uuid))"
            ),
            {"d": 777, "t": t},
        )
    db_session.commit()

    n = db_session.execute(
        text("SELECT COUNT(*) FROM department_tag_mappings WHERE dept_id = :d"),
        {"d": 777},
    ).scalar_one()
    assert n == 2


def test_fk_tag_cascade_delete(db_session):
    """AC#2: FK tag_id → tags.id ON DELETE CASCADE — 删 tag 级联删映射。"""
    tag_id = _seed_tag(db_session, "级联删除测试")
    db_session.commit()

    for dept in (1001, 1002, 1003):
        db_session.execute(
            text(
                "INSERT INTO department_tag_mappings (dept_id, tag_id) "
                "VALUES (:d, CAST(:t AS uuid))"
            ),
            {"d": dept, "t": tag_id},
        )
    db_session.commit()

    n_before = db_session.execute(
        text("SELECT COUNT(*) FROM department_tag_mappings WHERE tag_id = CAST(:t AS uuid)"),
        {"t": tag_id},
    ).scalar_one()
    assert n_before == 3

    db_session.execute(
        text("DELETE FROM tags WHERE id = CAST(:t AS uuid)"),
        {"t": tag_id},
    )
    db_session.commit()

    n_after = db_session.execute(
        text("SELECT COUNT(*) FROM department_tag_mappings WHERE tag_id = CAST(:t AS uuid)"),
        {"t": tag_id},
    ).scalar_one()
    assert n_after == 0, "FK ON DELETE CASCADE did not delete mapping rows"


def test_fk_rejects_dangling_tag(db_session):
    """FK 完整性：tag_id 指向不存在的 tag → IntegrityError。"""
    ghost = str(uuid.uuid4())
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "INSERT INTO department_tag_mappings (dept_id, tag_id) "
                "VALUES (:d, CAST(:t AS uuid))"
            ),
            {"d": 999, "t": ghost},
        )
        db_session.flush()
