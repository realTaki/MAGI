"""Allow partial provider-change notifications to store NULL fields.

Revision ID: 0.0.45
Revises: 0.0.44
Create Date: 2026-08-31
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import Text, inspect

revision = "0.0.45"
down_revision = "0.0.44"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table_name = "jobs_change_provider_notify"
    inspector = inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return
    with op.batch_alter_table(table_name) as batch:
        for column_name in ("provider", "api_key", "model"):
            batch.alter_column(column_name, existing_type=Text(), nullable=True)


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
