"""Drop ToolsBook source and allowed_roles columns.

Revision ID: 0.0.37
Revises: 0.0.36
Create Date: 2026-08-30
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "0.0.37"
down_revision = "0.0.36"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if "books_tools" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("books_tools")}
    indexes = {index["name"] for index in inspector.get_indexes("books_tools")}
    with op.batch_alter_table("books_tools") as batch:
        if "ix_books_tools_source" in indexes:
            batch.drop_index("ix_books_tools_source")
        if "source" in columns:
            batch.drop_column("source")
        if "allowed_roles" in columns:
            batch.drop_column("allowed_roles")


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
