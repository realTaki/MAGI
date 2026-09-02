"""Drop the FireTask JobBoard.

Revision ID: 0.0.44
Revises: 0.0.43
Create Date: 2026-08-31
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "0.0.44"
down_revision = "0.0.43"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "jobs_fire_task" in inspect(bind).get_table_names():
        op.drop_table("jobs_fire_task")


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
