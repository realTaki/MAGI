"""Drop unused CallLLM usage and model result columns.

Revision ID: 0.0.48
Revises: 0.0.47
Create Date: 2026-09-01
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "0.0.48"
down_revision = "0.0.47"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "jobs_call_llm" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("jobs_call_llm")}
    dropping = [name for name in ("usage", "model") if name in columns]
    if not dropping:
        return
    with op.batch_alter_table("jobs_call_llm") as batch:
        for name in dropping:
            batch.drop_column(name)


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
