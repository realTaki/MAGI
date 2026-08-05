
"""Alembic environment for MAGI's runtime database."""

from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Import every model so `alembic revision --autogenerate` sees the complete
# metadata. The runtime's init_orm() uses the same import set before invoking
# `alembic upgrade head`.
import magi.bus.models.local.action_item  # noqa: F401,E402
import magi.bus.models.local.contact  # noqa: F401,E402
import magi.bus.models.magis.eva_runtime  # noqa: F401,E402
import magi.bus.models.magis.magic  # noqa: F401,E402
import magi.bus.models.local.memory  # noqa: F401,E402
import magi.bus.models.magis.magis  # noqa: F401,E402
import magi.bus.models.local.setting  # noqa: F401,E402
import magi.bus.models.local.session  # noqa: F401,E402
import magi.bus.models.local.token_usage  # noqa: F401,E402
import magi.bus.models.local.tool  # noqa: F401,E402
import magi.bus.models.local.task_preset  # noqa: F401,E402
from magi.bus.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# State directory is <workspace>/memories — resolved from
# MAGI_WORKSPACE_DIR (K8s) or HOST_WORKSPACE_DIR (Local Profile).
# The programmatic runner sets the URL directly; this is only a
# convenience for CLI workflows, so we only fall back to the global
# ``state_dir()`` resolution when no URL has been provided by the
# caller.
from magi.launcher.paths import state_dir as _state_dir

if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite:///{Path(_state_dir()).resolve() / 'magi.db'}",
    )

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the configured SQLite database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
