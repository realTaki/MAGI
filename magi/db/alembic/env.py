
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
import magi.db.models_action_item  # noqa: F401,E402
import magi.db.models_contact  # noqa: F401,E402
import magi.db.models_eve_runtime  # noqa: F401,E402
import magi.db.models_magic  # noqa: F401,E402
import magi.db.models_magis  # noqa: F401,E402
import magi.db.models_setting  # noqa: F401,E402
import magi.db.models_token_usage  # noqa: F401,E402
import magi.agent.memory.self.models  # noqa: F401,E402
import magi.agent.memory.session.tables  # noqa: F401,E402
import magi.channels.tasks.models  # noqa: F401,E402
import magi.proactive.models  # noqa: F401,E402
from magi.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The CLI workflow can target the same workspace convention as the runtime:
# `MAGI_STATE_DIR=/path/to/state alembic ...`. The programmatic runner sets
# the URL directly, so this is only a convenience override for developers.
if state_dir := os.environ.get("MAGI_STATE_DIR"):
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite:///{Path(state_dir).resolve() / 'magi.db'}",
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
