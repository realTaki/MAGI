"""Drop the retired CallLLM thinking token budget.

Revision ID: 0.0.60
Revises: 0.0.59
Create Date: 2026-09-02
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "0.0.60"
down_revision = "0.0.59"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_name = "jobs_call_llm"
    if table_name not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if "thinking_tokens" in columns:
        with op.batch_alter_table(table_name) as batch:
            batch.drop_column("thinking_tokens")


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
