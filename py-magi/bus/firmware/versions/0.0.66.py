"""Add channel and address on DeliveryNotify.

Revision ID: 0.0.66
Revises: 0.0.65
Create Date: 2026-09-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0.0.66"
down_revision = "0.0.65"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    table_name = "jobs_delivery_notify"
    if table_name not in inspect(bind).get_table_names():
        return
    columns = {column["name"] for column in inspect(bind).get_columns(table_name)}
    with op.batch_alter_table(table_name) as batch:
        if "channel" not in columns:
            batch.add_column(
                sa.Column("channel", sa.Text(), nullable=False, server_default="")
            )
        if "address" not in columns:
            batch.add_column(
                sa.Column("address", sa.Text(), nullable=False, server_default="")
            )


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
