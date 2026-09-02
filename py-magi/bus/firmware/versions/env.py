"""Alembic env. Script location is this versions package."""

from __future__ import annotations

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection

from bus.firmware.versions.schema import firmware_metadata

config = context.config
target_metadata = firmware_metadata()


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is None:
        from sqlalchemy import engine_from_config

        connectable = engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
        with connectable.connect() as connection:
            _run(connection)
        return
    if isinstance(connectable, Connection):
        _run(connectable)
        return
    with connectable.connect() as connection:
        _run(connection)


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
