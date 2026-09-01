"""Require ContactNote update ownership and allow unfiltered note lists.

Revision ID: 0.0.56
Revises: 0.0.55
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0.0.56"
down_revision = "0.0.55"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    update_table = "jobs_update_contact_note"
    if update_table in tables:
        columns = {column["name"] for column in inspector.get_columns(update_table)}
        if "contact_id" not in columns:
            op.add_column(
                update_table,
                sa.Column("contact_id", sa.Integer(), nullable=False, server_default=sa.text("0")),
            )

    list_table = "jobs_list_contact_notes"
    if list_table in tables:
        columns = {column["name"] for column in inspector.get_columns(list_table)}
        if "kind" in columns:
            with op.batch_alter_table(list_table) as batch:
                batch.alter_column("kind", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
