"""Alembic env — uses sync DATABASE_URL from settings and imports all models."""

from logging.config import fileConfig

from alembic import context
import sqlalchemy as sa
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.core.db import Base

# Import all models so they register with Base.metadata
import app.models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.SYNC_DATABASE_URL)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        # Default alembic_version column is VARCHAR(32). Our revision names
        # are longer (e.g. "0004_figures_and_figure_regenerations" = 37
        # chars). SQLite ignored the length, Postgres rejects it with
        # StringDataRightTruncation. Widen the column.
        version_table_pk_type=sa.String(128),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # Self-heal: if alembic_version exists with the old default
        # VARCHAR(32) column, widen it before the migration tries to write
        # a 37-char revision name. This makes a fresh deploy onto a Postgres
        # that was previously left in a half-migrated state recoverable
        # without manual DROP SCHEMA.
        if connection.dialect.name == "postgresql":
            try:
                connection.execute(sa.text(
                    "ALTER TABLE alembic_version "
                    "ALTER COLUMN version_num TYPE VARCHAR(128)"
                ))
                connection.commit()
            except Exception:
                # Table doesn't exist yet (fresh DB) — fine, the
                # version_table_pk_type below will create it correctly.
                connection.rollback()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            version_table_pk_type=sa.String(128),
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
