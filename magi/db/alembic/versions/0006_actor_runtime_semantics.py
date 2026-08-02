"""Persist actor conversation, steering and LLM-attempt semantics.

Revision ID: 0006_actor_runtime_semantics
Revises: 0005_agent_bus
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_actor_runtime_semantics"
down_revision = "0005_agent_bus"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _add(table: str, column: sa.Column) -> None:
    if column.name not in _columns(table):
        op.add_column(table, column)


def upgrade() -> None:
    _add("agent_runs", sa.Column("conversation_id", sa.String(length=128), nullable=True))
    _add("agent_runs", sa.Column("correlation_id", sa.String(length=128), nullable=True))
    _add("agent_runs", sa.Column("version", sa.Integer(), nullable=False, server_default="0"))

    _add("agent_inbox", sa.Column("conversation_id", sa.String(length=128), nullable=True))
    _add("agent_inbox", sa.Column("correlation_id", sa.String(length=128), nullable=True))
    _add("agent_inbox", sa.Column("causation_id", sa.String(length=128), nullable=True))

    _add("run_inputs", sa.Column("received_seq", sa.Integer(), nullable=False, server_default="0"))
    _add("run_inputs", sa.Column("context_seq", sa.Integer(), nullable=True))
    _add("run_inputs", sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"))

    _add("llm_attempts", sa.Column("inbox_event_id", sa.String(length=64), nullable=True))
    _add("llm_attempts", sa.Column("provider", sa.String(length=64), nullable=True))
    _add("llm_attempts", sa.Column("model", sa.String(length=128), nullable=True))
    _add("llm_attempts", sa.Column("last_stream_seq", sa.Integer(), nullable=False, server_default="0"))

    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("agent_runs")}
    if "ix_agent_runs_conversation_status" not in indexes:
        op.create_index(
            "ix_agent_runs_conversation_status",
            "agent_runs",
            ["conversation_id", "status", "created_at"],
        )
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("agent_inbox")}
    if "ix_agent_inbox_conversation" not in indexes:
        op.create_index("ix_agent_inbox_conversation", "agent_inbox", ["conversation_id", "id"])
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("run_inputs")}
    if "ix_run_inputs_run_status_seq" not in indexes:
        op.create_index("ix_run_inputs_run_status_seq", "run_inputs", ["run_id", "status", "received_seq"])


def downgrade() -> None:
    # SQLite supports DROP COLUMN only on newer versions and a downgrade
    # would also need to rebuild indexes.  Runtime state is operational, so
    # retaining additive columns is safer than risking a destructive rebuild.
    pass
