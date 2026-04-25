"""Alembic migration environment.

D-1 修订：
- 从 NCMU_DB_URL 环境变量读取数据库 URL（避免 alembic.ini 硬编码密码）。
- URL 如果不含 `+psycopg` 方言前缀，自动改写为 psycopg (v3)，因为 TASK-22
  依赖列表只装 psycopg[binary]（不装 psycopg2）。
- target_metadata=None：Phase 1 迁移全部手写，不使用 autogenerate。Phase 3
  接 ORM 模型时再改为 `from ncmu_backend.db.models import Base; target_metadata = Base.metadata`。
"""
from logging.config import fileConfig
from os import environ

from sqlalchemy import engine_from_config, pool

from alembic import context


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolve_url() -> str:
    url = environ["NCMU_DB_URL"]
    if url.startswith("postgresql://"):
        # Force psycopg (v3) dialect — psycopg2 is NOT installed in this image.
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


config.set_main_option("sqlalchemy.url", _resolve_url())

target_metadata = None


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting to a database."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a real database connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
