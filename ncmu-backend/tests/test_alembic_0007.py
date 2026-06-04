"""TASK-PC1 — alembic 0007_init_personal_kb_tables pytest-postgresql 验证.

Verifies the 3 Phase 2C tables + 4 indexes land as specified, downgrade is
symmetric, the two CHECK constraints reject out-of-set values, app_id types
align with dify_apps.dify_app_id (spec §1.5 调和 #6), and that no mutex
trigger / mutex function exists in the DB (spec §1.5 调和 #4 — those land
together with tag_app_mappings during the 钉钉接入 phase).

Naming guarantees from CLAUDE-style "字面对账" reviews — these names appear
verbatim in the AC checklist; do not rename without updating spec/plan.
"""
from __future__ import annotations

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from conftest import _REPO_ROOT, _run_alembic


def _current_head_revision() -> str:
    """Return alembic's current head revision (revision-agnostic).

    Reading the head from the migration scripts keeps the upgrade-head
    assertion honest without hard-coding a version number that goes stale
    every time a new migration lands (0008 → 0011 → …). script_location is
    set absolutely so the lookup is independent of the test's cwd.
    """
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()


PERSONAL_KB_TABLES = {
    "app_owners",
    "personal_kb_applications",
    "personal_kb_application_files",
}

EXPECTED_INDEXES = {
    "ix_app_owners_user",
    "ix_personal_kb_app_user_status",
    "ix_pkaf_app",
    "ix_pkaf_sha",
}

# Stable UUID literal — keeps SQL readable without pulling in a uuid module
# at module top (the literal is only used as a foreign-key target for the
# users.id rows seeded inside individual tests).
_TEST_USER_UUID = "aa000007-0000-4000-8000-000000000001"


def _list_tables(session: Session) -> set[str]:
    return set(
        session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
        )
        .scalars()
        .all()
    )


def _list_indexes(session: Session) -> set[str]:
    return set(
        session.execute(
            text(
                "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
            )
        )
        .scalars()
        .all()
    )


def _seed_user(session: Session, uid: str = _TEST_USER_UUID) -> str:
    """Insert one users row so FK constraints have a real target.

    ON CONFLICT DO NOTHING lets multiple inserts in a single test share
    the same UUID without tripping the PK uniqueness.
    """
    session.execute(
        text(
            "INSERT INTO users (id, name) "
            "VALUES (CAST(:uid AS uuid), :name) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"uid": uid, "name": "pc1-test-user"},
    )
    session.commit()
    return uid


# ─── AC#1 + AC#6 ────────────────────────────────────────────────────────


def test_upgrade_creates_tables(db_session):
    """AC#1: alembic upgrade head creates the 3 Phase 2C tables + 4 indexes.

    The db_session fixture already ran ``alembic upgrade head`` against the
    ephemeral DB; this test just asserts the resulting public schema.
    """
    tables = _list_tables(db_session)
    assert PERSONAL_KB_TABLES.issubset(tables), (
        f"missing tables after upgrade: {PERSONAL_KB_TABLES - tables}; "
        f"actual public schema: {sorted(tables)}"
    )

    # Revision-agnostic: assert the fixture's `upgrade head` actually left the
    # DB at the migration tree's real head, whatever number that currently is.
    # (Was a hard-coded ``== "0008"`` which went stale once 0009/0010/0011
    # landed — the table/index assertions above are the real Phase 2C coverage.)
    version = db_session.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalar_one()
    head = _current_head_revision()
    assert version == head, f"expected alembic_version={head} (head), got {version}"

    # AC#6: 4 named indexes exist
    indexes = _list_indexes(db_session)
    assert EXPECTED_INDEXES.issubset(indexes), (
        f"missing indexes after upgrade: {EXPECTED_INDEXES - indexes}; "
        f"actual indexes: {sorted(indexes)}"
    )


def test_downgrade_drops_tables(test_db_url):
    """AC#1: alembic downgrade 0008→0007→0006 对称回滚（3 表 + 4 索引全删）.

    Goes through a full upgrade-head/downgrade-one-step cycle on a fresh DB
    so the assertion captures both the table drop (PostgreSQL would cascade
    drop the indexes implicitly) and the explicit ``op.drop_index`` calls
    in the migration body.
    """
    _run_alembic(test_db_url, "upgrade", "head")
    # REWORK-PC2-FIX-C1: explicit target revision 0006 (instead of "-1") so
    # this test stays correct as new migrations land beyond 0007 (e.g. 0008+).
    _run_alembic(test_db_url, "downgrade", "0006")

    engine = create_engine(test_db_url, future=True)
    try:
        with Session(engine) as session:
            tables = _list_tables(session)
            residual_tables = tables & PERSONAL_KB_TABLES
            assert not residual_tables, (
                f"downgrade left tables behind: {residual_tables}"
            )

            indexes = _list_indexes(session)
            residual_indexes = indexes & EXPECTED_INDEXES
            assert not residual_indexes, (
                f"downgrade left indexes behind: {residual_indexes}"
            )

            version = session.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            assert version == "0006", (
                f"expected alembic_version=0006 after downgrade to 0006, got {version}"
            )
    finally:
        engine.dispose()


# ─── AC#2: 5-state CHECK ────────────────────────────────────────────────


def test_status_check_constraint(db_session):
    """AC#2: personal_kb_applications.status CHECK accepts 5 / rejects others.

    Inserts each of the 5 allowed values (so the test fails the day any of
    them silently disappears from the constraint expression) then asserts
    that a representative non-allowed value raises IntegrityError.
    """
    uid = _seed_user(db_session)

    allowed = ("pending", "in_progress", "done", "rejected", "cancelled")
    for status_val in allowed:
        db_session.execute(
            text(
                "INSERT INTO personal_kb_applications (user_id, status) "
                "VALUES (CAST(:uid AS uuid), :st)"
            ),
            {"uid": uid, "st": status_val},
        )
    db_session.commit()

    n_rows = db_session.execute(
        text("SELECT COUNT(*) FROM personal_kb_applications")
    ).scalar_one()
    assert n_rows == len(allowed), (
        f"expected {len(allowed)} rows for the 5 allowed states, got {n_rows}"
    )

    # Reject a plausible-but-disallowed alternate spelling (Phase 2C AC#2
    # spells out 'processing' / 'dispatched' as red-flag variants).
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "INSERT INTO personal_kb_applications (user_id, status) "
                "VALUES (CAST(:uid AS uuid), 'processing')"
            ),
            {"uid": uid},
        )
        db_session.flush()


# ─── AC#4: 3-value visibility CHECK ─────────────────────────────────────


def test_visibility_check_constraint(db_session):
    """AC#4: app_owners.visibility CHECK accepts 3 / rejects others.

    'shared' / 'public' are reserved for later phases but the schema accepts
    them now so the column doesn't need rewriting when those land.
    """
    uid = _seed_user(db_session)

    allowed = ("owner_only", "shared", "public")
    for i, vis in enumerate(allowed):
        db_session.execute(
            text(
                "INSERT INTO app_owners (app_id, owner_user_id, visibility) "
                "VALUES (:aid, CAST(:uid AS uuid), :vis)"
            ),
            {"aid": f"app-vis-{i}", "uid": uid, "vis": vis},
        )
    db_session.commit()

    n_rows = db_session.execute(
        text("SELECT COUNT(*) FROM app_owners")
    ).scalar_one()
    assert n_rows == len(allowed)

    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "INSERT INTO app_owners (app_id, owner_user_id, visibility) "
                "VALUES ('app-bogus', CAST(:uid AS uuid), 'bogus')"
            ),
            {"uid": uid},
        )
        db_session.flush()


# ─── AC#4b: app_id ↔ dify_apps.dify_app_id 类型对齐 ─────────────────────


def test_app_id_type_alignment_with_dify_apps(db_session, test_db_url):
    """AC#4b: app_owners.app_id matches dify_apps.dify_app_id field type.

    spec §1.5 调和 #6: errata-12 §2.2 wrote ``UUID PRIMARY KEY`` as logical
    pseudocode (the same erratum self-noted at §2.3 line 179 that the spec
    phase would land the real types). The real upstream column —
    ``dify_apps.dify_app_id`` from alembic 0004 — is VARCHAR(64), so this
    migration follows. Reflection picks up the live PostgreSQL column type
    via information_schema and surfaces it as a SQLAlchemy String(64),
    letting the assertion compare both the runtime class and the length.

    The db_session fixture also runs alembic upgrade head; we reuse
    test_db_url to open a fresh engine for MetaData.reflect (which needs
    a live connection separate from the session's transactional scope).
    """
    engine = create_engine(test_db_url, future=True)
    try:
        meta = MetaData()
        meta.reflect(bind=engine, only=["app_owners", "dify_apps"])

        ao_col = meta.tables["app_owners"].c.app_id
        da_col = meta.tables["dify_apps"].c.dify_app_id

        assert type(ao_col.type) is type(da_col.type), (
            f"type mismatch: app_owners.app_id={type(ao_col.type).__name__} "
            f"vs dify_apps.dify_app_id={type(da_col.type).__name__}"
        )
        assert ao_col.type.length == 64, (
            f"app_owners.app_id length={ao_col.type.length}, expected 64"
        )
        assert da_col.type.length == 64, (
            f"dify_apps.dify_app_id length={da_col.type.length}, expected 64"
        )
    finally:
        engine.dispose()


# ─── AC#5: FK CASCADE ───────────────────────────────────────────────────


def test_application_files_fk_cascade(db_session):
    """AC#5: personal_kb_application_files.application_id ON DELETE CASCADE.

    Delete the parent application → all 3 child file rows are gone in the
    same statement (no manual sweep required at the application layer).
    """
    uid = _seed_user(db_session)

    application_id = db_session.execute(
        text(
            "INSERT INTO personal_kb_applications (user_id) "
            "VALUES (CAST(:uid AS uuid)) "
            "RETURNING id"
        ),
        {"uid": uid},
    ).scalar_one()

    for i in range(3):
        db_session.execute(
            text(
                "INSERT INTO personal_kb_application_files "
                "(application_id, original_filename, storage_path, file_size_bytes) "
                "VALUES (:aid, :fn, :sp, :sz)"
            ),
            {
                "aid": application_id,
                "fn": f"doc{i}.pdf",
                "sp": f"/tmp/pkb/{i}.pdf",
                "sz": 1024 * (i + 1),
            },
        )
    db_session.commit()

    n_before = db_session.execute(
        text(
            "SELECT COUNT(*) FROM personal_kb_application_files "
            "WHERE application_id = :aid"
        ),
        {"aid": application_id},
    ).scalar_one()
    assert n_before == 3

    db_session.execute(
        text("DELETE FROM personal_kb_applications WHERE id = :aid"),
        {"aid": application_id},
    )
    db_session.commit()

    n_after = db_session.execute(
        text(
            "SELECT COUNT(*) FROM personal_kb_application_files "
            "WHERE application_id = :aid"
        ),
        {"aid": application_id},
    ).scalar_one()
    assert n_after == 0, "FK ON DELETE CASCADE did not delete child rows"


# ─── AC#3: trigger 推迟字面 DB 对账 ─────────────────────────────────────


def test_no_mutex_trigger_or_function(db_session):
    """AC#3: no mutex trigger / function exists post-upgrade.

    spec §1.5 调和 #4 — the互斥 routing trigger lands together with
    tag_app_mappings during钉钉接入. The migration must NOT create those
    objects this phase. Asserting against pg_trigger / pg_proc catches any
    accidental leak even if a future edit forgets to grep the source.
    """
    triggers = (
        db_session.execute(
            text(
                "SELECT tgname FROM pg_trigger "
                "WHERE tgname IN ('trg_tag_app_mappings_mutex', "
                "                 'trg_app_owners_mutex')"
            )
        )
        .scalars()
        .all()
    )
    assert triggers == [], f"unexpected mutex triggers present: {triggers}"

    functions = (
        db_session.execute(
            text(
                "SELECT proname FROM pg_proc "
                "WHERE proname LIKE 'enforce_%_mutex'"
            )
        )
        .scalars()
        .all()
    )
    assert functions == [], (
        f"unexpected enforce_*_mutex functions present: {functions}"
    )
