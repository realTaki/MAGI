"""Drop the explicit CallLLM completion limit.

Revision ID: 0.0.61
Revises: 0.0.60
Create Date: 2026-09-02
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "0.0.61"
down_revision = "0.0.60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    table_name = "jobs_call_llm"
    if table_name not in inspect(bind).get_table_names():
        return
    columns = {column["name"] for column in inspect(bind).get_columns(table_name)}
    if "max_tokens" in columns:
        with op.batch_alter_table(table_name) as batch:
            batch.drop_column("max_tokens")


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
