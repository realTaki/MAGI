"""Return the created Conversation id instead of a serialized record.

Revision ID: 0.0.3
Revises: 0.0.2
Create Date: 2026-08-27
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import Column, Integer, inspect

revision = "0.0.3"
down_revision = "0.0.2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in inspect(bind).get_columns("jobs_create_conversation")}
    if "conversation_id" not in existing:
        op.add_column("jobs_create_conversation", Column("conversation_id", Integer, nullable=True))


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
