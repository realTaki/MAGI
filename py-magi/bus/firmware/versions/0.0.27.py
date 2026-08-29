"""Add Conversation description and info text fields.

Revision ID: 0.0.27
Revises: 0.0.26
Create Date: 2026-08-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0.0.27"
down_revision = "0.0.26"
branch_labels = None
depends_on = None


def _add_text_column(table_name: str, column_name: str) -> None:
    inspector = inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name in columns:
        return
    with op.batch_alter_table(table_name) as batch:
        batch.add_column(sa.Column(column_name, sa.Text(), nullable=False, server_default=""))


def upgrade() -> None:
    for table_name in ("books_conversations", "jobs_create_conversation"):
        _add_text_column(table_name, "description")
        _add_text_column(table_name, "info")


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
