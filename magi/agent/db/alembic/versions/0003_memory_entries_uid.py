"""Rename memory_entries.employee_id -> uid (D.23 owner-identity fix).

Pre-D.23 databases that were stamped at 0002 kept the legacy
``employee_id`` column on ``memory_entries`` — the owner-identity
rename only ran on the legacy inline-migration path, which
non-legacy databases skip. This closes the gap idempotently so
``upgrade head`` is safe on both fresh and already-migrated DBs.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import inspect, text

revision = "0003_memory_uid"
down_revision = "0002_fts5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("memory_entries")}
    if "employee_id" in cols and "uid" not in cols:
        bind.execute(
            text("ALTER TABLE memory_entries RENAME COLUMN employee_id TO uid")
        )


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("memory_entries")}
    if "uid" in cols and "employee_id" not in cols:
        bind.execute(
            text("ALTER TABLE memory_entries RENAME COLUMN uid TO employee_id")
        )
