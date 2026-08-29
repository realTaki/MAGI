"""Split Memory content into topic and detail.

Revision ID: 0.0.34
Revises: 0.0.33
Create Date: 2026-08-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision = "0.0.34"
down_revision = "0.0.33"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    inspector = inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _add_text_column(table_name: str, column_name: str) -> None:
    if column_name in _columns(table_name):
        return
    with op.batch_alter_table(table_name) as batch:
        batch.add_column(sa.Column(column_name, sa.Text(), nullable=False, server_default=""))


def _drop_column(table_name: str, column_name: str) -> None:
    if column_name not in _columns(table_name):
        return
    with op.batch_alter_table(table_name) as batch:
        batch.drop_column(column_name)


def upgrade() -> None:
    for table_name in ("books_memories", "jobs_create_memory", "jobs_update_memory"):
        columns = _columns(table_name)
        if not columns:
            continue
        _add_text_column(table_name, "topic")
        _add_text_column(table_name, "detail")
        if "content" in _columns(table_name):
            op.execute(text(f"UPDATE {table_name} SET detail = content WHERE content IS NOT NULL"))
            _drop_column(table_name, "content")


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
