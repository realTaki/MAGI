"""Add Memory.archived and list/update job columns.

Revision ID: 0.0.33
Revises: 0.0.32
Create Date: 2026-08-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0.0.33"
down_revision = "0.0.32"
branch_labels = None
depends_on = None


def _add_bool_column(table_name: str, column_name: str) -> None:
    inspector = inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name in columns:
        return
    with op.batch_alter_table(table_name) as batch:
        batch.add_column(sa.Column(column_name, sa.Boolean(), nullable=False, server_default=sa.false()))


def upgrade() -> None:
    _add_bool_column("books_memories", "archived")
    _add_bool_column("jobs_update_memory", "archived")
    _add_bool_column("jobs_list_memories", "include_archived")


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
