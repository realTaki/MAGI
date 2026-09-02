"""Drop the unused RunTool error_code column.

Revision ID: 0.0.22
Revises: 0.0.21
Create Date: 2026-08-28
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "0.0.22"
down_revision = "0.0.21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "jobs_run_tool" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("jobs_run_tool")}
    if "error_code" not in columns:
        return
    with op.batch_alter_table("jobs_run_tool") as batch:
        batch.drop_column("error_code")


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
