"""Remove the obsolete delete-prompt Job table.

Revision ID: 0.0.19
Revises: 0.0.18
Create Date: 2026-08-28
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "0.0.19"
down_revision = "0.0.18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "jobs_delete_prompt" in inspect(bind).get_table_names():
        op.drop_table("jobs_delete_prompt")


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
