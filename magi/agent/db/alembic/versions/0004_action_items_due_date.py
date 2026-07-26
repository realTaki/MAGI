"""Add due_date column to action_items.

Optional deadline for action items — null means "no deadline".
C4 EVE-driven follow-ups can carry a due date; operator-authored
rows may leave it unset.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import Column, DateTime, inspect

revision = "0004_action_items_due_date"
down_revision = "0003_memory_uid"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("action_items")}
    if "due_date" not in cols:
        op.add_column("action_items", Column("due_date", DateTime, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("action_items")}
    if "due_date" in cols:
        op.drop_column("action_items", "due_date")
