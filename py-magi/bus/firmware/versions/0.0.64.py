"""Identify conversations by channel + delivery_address.

Revision ID: 0.0.64
Revises: 0.0.63
Create Date: 2026-09-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0.0.64"
down_revision = "0.0.63"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_name = "books_conversations"
    if table_name in inspector.get_table_names():
        names = {item["name"] for item in inspector.get_unique_constraints(table_name)}
        names.update(item["name"] for item in inspector.get_indexes(table_name))
        if "uq_books_conversations_endpoint" not in names:
            with op.batch_alter_table(table_name) as batch:
                batch.create_unique_constraint(
                    "uq_books_conversations_endpoint",
                    ["channel", "delivery_address"],
                )

    table_name = "jobs_chat_notify"
    if table_name not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspect(bind).get_columns(table_name)}
    with op.batch_alter_table(table_name) as batch:
        if "channel" not in columns:
            batch.add_column(
                sa.Column("channel", sa.Text(), nullable=False, server_default="")
            )
        if "delivery_address" not in columns:
            batch.add_column(
                sa.Column(
                    "delivery_address", sa.Text(), nullable=False, server_default=""
                )
            )


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
