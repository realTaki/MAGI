"""Return the appended Message id instead of a serialized record.

Revision ID: 0.0.4
Revises: 0.0.3
Create Date: 2026-08-27
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import Column, Integer, inspect

revision = "0.0.4"
down_revision = "0.0.3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in inspect(bind).get_columns("jobs_append_message")}
    if "message_id" not in existing:
        op.add_column("jobs_append_message", Column("message_id", Integer, nullable=True))


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
