"""Drop unused RunTool caller-identity columns.

Revision ID: 0.0.23
Revises: 0.0.22
Create Date: 2026-08-28
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "0.0.23"
down_revision = "0.0.22"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "jobs_run_tool" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("jobs_run_tool")}
    dropping = [name for name in ("contact_id", "channel") if name in columns]
    if not dropping:
        return
    with op.batch_alter_table("jobs_run_tool") as batch:
        for name in dropping:
            batch.drop_column(name)


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
