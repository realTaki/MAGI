"""Replace Message role with contact_id.

Revision ID: 0.0.31
Revises: 0.0.30
Create Date: 2026-08-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0.0.31"
down_revision = "0.0.30"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    inspector = inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    message_columns = _columns("books_messages")
    if message_columns:
        with op.batch_alter_table("books_messages") as batch:
            if "contact_id" not in message_columns:
                batch.add_column(
                    sa.Column(
                        "contact_id",
                        sa.Integer(),
                        sa.ForeignKey("books_contacts.id"),
                        nullable=False,
                        server_default="0",
                    )
                )
            if "role" in message_columns:
                batch.drop_column("role")

    job_columns = _columns("jobs_append_message")
    if job_columns:
        with op.batch_alter_table("jobs_append_message") as batch:
            if "contact_id" not in job_columns:
                batch.add_column(
                    sa.Column("contact_id", sa.Integer(), nullable=False, server_default="0")
                )
            if "role" in job_columns:
                batch.drop_column("role")


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
