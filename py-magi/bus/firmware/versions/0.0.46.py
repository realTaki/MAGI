"""Drop unused RunTool conversation_id.

Revision ID: 0.0.46
Revises: 0.0.45
Create Date: 2026-08-31
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "0.0.46"
down_revision = "0.0.45"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "jobs_run_tool" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("jobs_run_tool")}
    if "conversation_id" not in columns:
        return
    with op.batch_alter_table("jobs_run_tool") as batch:
        batch.drop_column("conversation_id")


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
