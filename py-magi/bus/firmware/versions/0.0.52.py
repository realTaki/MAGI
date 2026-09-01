"""Align the durable LLM request fields with LiteLLM.

Revision ID: 0.0.52
Revises: 0.0.51
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0.0.52"
down_revision = "0.0.51"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    table_name = "jobs_call_llm"
    if table_name not in inspect(bind).get_table_names():
        return
    columns = {column["name"] for column in inspect(bind).get_columns(table_name)}
    if "max_output_tokens" in columns:
        with op.batch_alter_table(table_name) as batch:
            batch.alter_column("max_output_tokens", new_column_name="max_tokens", existing_type=sa.Integer())

    jobs = sa.table(
        table_name,
        sa.column("id", sa.Integer()),
        sa.column("messages", sa.JSON()),
        sa.column("message", sa.JSON()),
    )
    for row in bind.execute(sa.select(jobs)).mappings():
        messages = []
        for message in row["messages"] or ():
            if isinstance(message, dict) and "text" in message:
                message = dict(message)
                message["content"] = message.pop("text")
            messages.append(message)
        message = row["message"]
        if isinstance(message, dict) and "text" in message:
            message = dict(message)
            message["content"] = message.pop("text")
        bind.execute(
            jobs.update().where(jobs.c.id == row["id"]).values(messages=messages, message=message)
        )


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
