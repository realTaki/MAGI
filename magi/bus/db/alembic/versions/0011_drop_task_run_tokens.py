"""Drop ``task_runs.input_tokens`` / ``task_runs.output_tokens``.

Revision ID: 0011_drop_task_run_tokens
Revises: 0010_add_conversation_summary

Token accounting already lives in ``token_usage`` (populated via
``TokenUsageBook`` and ``magi.agent.token_usage``). Task-runs don't
own a separate budget — leaving the columns on ``task_runs`` invites
double-bookkeeping drift, so they go.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_drop_task_run_tokens"
down_revision: str | Sequence[str] | None = "0010_add_conversation_summary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("task_runs")
    }
    with op.batch_alter_table("task_runs") as batch:
        for name in ("input_tokens", "output_tokens"):
            if name in columns:
                batch.drop_column(name)


def downgrade() -> None:
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("task_runs")
    }
    with op.batch_alter_table("task_runs") as batch:
        if "input_tokens" not in columns:
            batch.add_column(
                sa.Column(
                    "input_tokens",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                )
            )
        if "output_tokens" not in columns:
            batch.add_column(
                sa.Column(
                    "output_tokens",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                )
            )
