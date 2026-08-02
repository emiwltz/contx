"""Alembic environment for packaged CONTX migrations."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, engine_from_config, pool

from contx.db import models as persistence_models  # noqa: F401
from contx.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Render migrations without a live connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations(connection: Connection) -> None:
    """Apply migrations on the caller-owned transaction."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Use an injected connection or create one for Alembic CLI usage."""
    injected_connection = config.attributes.get("connection")
    if injected_connection is not None:
        run_migrations(injected_connection)
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        run_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
