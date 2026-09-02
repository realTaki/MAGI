"""Allow listing only the newest conversation messages.

Revision ID: 0.0.63
Revises: 0.0.62
Create Date: 2026-09-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0.0.63"
down_revision = "0.0.62"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    table_name = "jobs_list_conversation_messages"
    if table_name not in inspect(bind).get_table_names():
        return
    columns = {column["name"] for column in inspect(bind).get_columns(table_name)}
    if "last_n" not in columns:
        with op.batch_alter_table(table_name) as batch:
            batch.add_column(sa.Column("last_n", sa.Integer(), nullable=True))


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
