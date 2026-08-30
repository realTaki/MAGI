"""Drop TokenUsage Book and RecordTokenUsage Job tables.

Revision ID: 0.0.35
Revises: 0.0.34
Create Date: 2026-08-30
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "0.0.35"
down_revision = "0.0.34"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for table_name in ("jobs_record_token_usage", "books_token_usage"):
        if table_name in tables:
            op.drop_table(table_name)


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
