"""Rename Conversation title to topic.

Revision ID: 0.0.30
Revises: 0.0.29
Create Date: 2026-08-29
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import Text, inspect

revision = "0.0.30"
down_revision = "0.0.29"
branch_labels = None
depends_on = None


def _rename_title(table_name: str) -> None:
    inspector = inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if "title" not in columns or "topic" in columns:
        return
    with op.batch_alter_table(table_name) as batch:
        batch.alter_column(
            "title",
            new_column_name="topic",
            existing_type=Text(),
        )


def upgrade() -> None:
    _rename_title("books_conversations")
    _rename_title("jobs_create_conversation")


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
