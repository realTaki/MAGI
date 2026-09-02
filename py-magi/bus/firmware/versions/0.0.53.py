"""Replace SetToolJob with SetToolsJob.

Revision ID: 0.0.53
Revises: 0.0.52
Create Date: 2026-09-01
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "0.0.53"
down_revision = "0.0.52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "jobs_set_tool" in inspector.get_table_names():
        op.drop_table("jobs_set_tool")


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
