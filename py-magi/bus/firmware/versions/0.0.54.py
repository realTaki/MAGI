"""Drop CallLLM finish_reason; abnormal stops use Result.error.

Revision ID: 0.0.54
Revises: 0.0.53
Create Date: 2026-09-01
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "0.0.54"
down_revision = "0.0.53"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "jobs_call_llm" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("jobs_call_llm")}
    if "finish_reason" not in columns:
        return
    with op.batch_alter_table("jobs_call_llm") as batch:
        batch.drop_column("finish_reason")


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
