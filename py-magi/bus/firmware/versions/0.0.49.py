"""Store each tool invocation as one LLM tool-call value.

Revision ID: 0.0.49
Revises: 0.0.48
Create Date: 2026-08-31
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0.0.49"
down_revision = "0.0.48"
branch_labels = None
depends_on = None


def _call(row: Any) -> dict[str, Any]:
    return {
        "tool_call_id": str(row["tool_call_id"] or ""),
        "name": str(row["name"] or ""),
        "arguments": row["arguments"] if isinstance(row["arguments"], dict) else {},
    }


def upgrade() -> None:
    bind = op.get_bind()
    table_name = "jobs_run_tool"
    if table_name not in inspect(bind).get_table_names():
        return
    columns = {column["name"] for column in inspect(bind).get_columns(table_name)}
    if "call" in columns:
        return
    required = {"id", "name", "tool_call_id", "arguments"}
    if not required <= columns:
        return

    with op.batch_alter_table(table_name) as batch:
        batch.add_column(sa.Column("call", sa.JSON(), nullable=True))

    jobs = sa.table(
        table_name,
        sa.column("id", sa.Integer()),
        sa.column("name", sa.Text()),
        sa.column("tool_call_id", sa.Text()),
        sa.column("arguments", sa.JSON()),
        sa.column("call", sa.JSON()),
    )
    for row in bind.execute(sa.select(jobs)).mappings():
        bind.execute(jobs.update().where(jobs.c.id == row["id"]).values(call=_call(row)))

    with op.batch_alter_table(table_name) as batch:
        batch.alter_column("call", nullable=False, existing_type=sa.JSON())
        batch.drop_column("name")
        batch.drop_column("tool_call_id")
        batch.drop_column("arguments")


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
