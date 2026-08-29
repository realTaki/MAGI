"""Remove Task execution-state columns.

Revision ID: 0.0.18
Revises: 0.0.17
Create Date: 2026-08-28
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "0.0.18"
down_revision = "0.0.17"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "books_tasks" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("books_tasks")}
    indexes = {index["name"] for index in inspector.get_indexes("books_tasks")}
    with op.batch_alter_table("books_tasks") as batch:
        if "ix_books_tasks_enabled_last_run" in indexes:
            batch.drop_index("ix_books_tasks_enabled_last_run")
        if "consecutive_failures" in columns:
            batch.drop_column("consecutive_failures")
        if "last_run_at" in columns:
            batch.drop_column("last_run_at")


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
