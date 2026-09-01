"""Restore the catalog's internal name index after definition nesting.

Revision ID: 0.0.51
Revises: 0.0.50
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0.0.51"
down_revision = "0.0.50"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    table_name = "books_tools"
    inspector = inspect(bind)
    if table_name not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if "name" in columns or "definition" not in columns:
        return

    with op.batch_alter_table(table_name) as batch:
        batch.add_column(sa.Column("name", sa.Text(), nullable=True))

    tools = sa.table(
        table_name,
        sa.column("id", sa.Integer()),
        sa.column("name", sa.Text()),
        sa.column("definition", sa.JSON()),
    )
    for row in bind.execute(sa.select(tools)).mappings():
        definition = row["definition"] if isinstance(row["definition"], dict) else {}
        bind.execute(
            tools.update().where(tools.c.id == row["id"]).values(name=str(definition.get("name") or ""))
        )

    with op.batch_alter_table(table_name) as batch:
        batch.alter_column("name", nullable=False, existing_type=sa.Text())
        batch.create_unique_constraint("uq_books_tools_name", ["name"])


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
