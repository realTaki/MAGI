"""Complete durable actor transcript and A2A invocation metadata.

Revision ID: 0007_actor_runtime_completion
Revises: 0006_actor_runtime_semantics
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_actor_runtime_completion"
down_revision = "0006_actor_runtime_semantics"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _add(table: str, column: sa.Column) -> None:
    if column.name not in _columns(table):
        op.add_column(table, column)


def upgrade() -> None:
    _add("chat_messages", sa.Column("content_blocks", sa.JSON(), nullable=True))
    _add("chat_messages", sa.Column("run_id", sa.String(length=64), nullable=True))
    _add("chat_messages", sa.Column("llm_attempt_id", sa.String(length=128), nullable=True))
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("chat_messages")}
    if "ix_chat_messages_run_id" not in indexes:
        op.create_index("ix_chat_messages_run_id", "chat_messages", ["run_id"])

    _add("a2a_invocations", sa.Column("tool_call_id", sa.String(length=128), nullable=True))
    _add("a2a_invocations", sa.Column("request_event_id", sa.String(length=128), nullable=True))
    _add("a2a_invocations", sa.Column("reply_to", sa.String(length=128), nullable=True))
    _add("a2a_invocations", sa.Column("expect_reply", sa.Boolean(), nullable=False, server_default=sa.false()))
    _add("a2a_invocations", sa.Column("deadline_at", sa.DateTime(), nullable=True))
    _add("a2a_invocations", sa.Column("idempotency_key", sa.String(length=160), nullable=True))
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("a2a_invocations")}
    for name, column in (
        ("ux_a2a_invocations_tool_call", "tool_call_id"),
        ("ux_a2a_invocations_request_event", "request_event_id"),
        ("ux_a2a_invocations_idempotency", "idempotency_key"),
    ):
        if name not in indexes:
            op.create_index(name, "a2a_invocations", [column], unique=True)


def downgrade() -> None:
    pass
