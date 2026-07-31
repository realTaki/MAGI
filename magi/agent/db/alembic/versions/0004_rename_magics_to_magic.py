"""Legacy compatibility bridge for an abandoned ``magics`` table name.

Some pre-baseline development databases used ``magics`` for the society
table.  The current naming is completed by
``0007_swap_magic_magis_tables``; this revision merely normalizes that
obsolete spelling to the legacy ``magic`` table shape expected by the
swap.  Fresh databases created from ``0001_baseline`` already have that
shape, so this migration is intentionally a no-op for them.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0004_rename_magics_to_magic"
# This must run after the runtime table migration, then the table-name swap
# can update all three related tables atomically.  Keeping one linear chain
# is essential: Alembic cannot upgrade a fresh installation through two heads.
down_revision = "0007_eve_runtimes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())

    if "magic" in existing:
        # Already renamed — nothing to do.
        return

    if "magics" not in existing:
        # Neither legacy spelling exists.  Leave the database untouched;
        # a fresh baseline already provides the legacy shape for the next
        # migration in the chain.
        return

    # SQLite's ALTER TABLE RENAME preserves FK references automatically.
    op.rename_table("magics", "magic")


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())

    if "magics" in existing:
        # Already at the legacy name.
        return

    if "magic" not in existing:
        return

    op.rename_table("magic", "magics")
