"""Remove the obsolete scheduled-task run ledger.

Revision ID: 0.0.17
Revises: 0.0.16
Create Date: 2026-08-28
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "0.0.17"
down_revision = "0.0.16"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "books_task_runs" in inspect(bind).get_table_names():
        op.drop_table("books_task_runs")


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
