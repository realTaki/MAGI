"""Minimal alembic env for MAGI's programmatic upgrade path.

This file is **not an operator-facing script**.  It exists only because
alembic's ``command.upgrade`` unconditionally loads ``env.py`` from the
``script_location`` directory; :func:`magi.bus.db.schema.upgrade_schema`
sets ``script_location`` to this package and calls ``command.upgrade``
programmatically before a BUS is opened.

What this file does:

1. Imports :class:`magi.bus.db.base.Base` after pulling in the ORM
   tables from :mod:`magi.bus.guild`, :mod:`magi.bus.library.local`, and
   :mod:`magi.bus.library.magis`
   (their ``__init__`` modules do the import side-effects so every
   table is registered on ``Base.metadata``).
2. Sets ``target_metadata = Base.metadata`` so alembic can walk it.
3. Reuses the connection supplied by ``upgrade_schema``.  This preserves
   the BUS engine's SQLite pragmas and transaction semantics.  A standalone
   Alembic invocation can still fall back to ``sqlalchemy.url``.

Offline mode (``--sql``) is supported but never invoked in normal boot
— ``upgrade_schema`` always runs online against the live SQLite file.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Ensure the project root is on sys.path so ``import magi`` resolves
# no matter which CWD alembic was launched from.
# ``env.py`` lives at ``magi/bus/db/alembic/env.py`` — 4 levels deep
# under the project root (``magi/`` package root + 3 sub-packages).
PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Imports every ORM module via the two package __init__ files; see
# module docstring.  Do this BEFORE reading ``target_metadata`` so
# every table is registered before alembic walks the metadata.
import magi.bus.guild  # noqa: F401  (side-effect: registers guild tables)
import magi.bus.library.local  # noqa: F401  (side-effect: registers local tables)
import magi.bus.library.magis  # noqa: F401  (side-effect: registers MAGIS tables)

from magi.bus.db.base import Base  # noqa: E402  (must come after the imports above)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout (used only for ``alembic --sql`` style dry runs)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect to the live DB and run every pending migration."""
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        context.configure(connection=supplied_connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
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
