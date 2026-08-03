"""Persist agent_run metadata: deadline, iteration count, expected ids, token usage.

Revision ID: 0011_agent_run_metadata
Revises: 0010_tool_call_ordinal

Architecture §7.3 of docs/MAGI_BUS_CENTRIC_ARCHITECTURE.md
asks for these fields on ``agent_runs`` so the actor worker's
accounting surfaces can answer:

  - "what tool calls does this run still expect?" (``expected_tool_call_ids``)
  - "what A2A invocations are pending?" (``expected_a2a_invocation_ids``)
  - "how many LLM steps have we done?" (``iteration_count``)
  - "what's the wall-clock deadline?" (``deadline_at``)
  - "what was the last step's token usage?" (``token_usage``)

These columns are denormalised projections of fields already in
``continuation`` / ``attempt_result``. The migration is
additive-only — pre-0011 rows keep their ``None`` ordinal /
iteration_count / token_usage, and ``load_tool_continuation`` etc.
keep reading from the canonical JSON. The new columns are a
query shortcut for monitoring and a hard deadline gate for the
actor worker (``run.deadline_at`` check).

All columns are nullable + additive. ``deadline_at`` is a
wall-clock timestamp; ``AgentMessage.deadline_at`` propagates
through ``BusStore.publish_agent_message``. ``token_usage`` is
the JSON shape the LLM provider returns; opaque to the runtime.
``expected_tool_call_ids`` / ``expected_a2a_invocation_ids`` are
the list of tool_call_ids / invocation_ids that the actor is
still waiting on; cleared when the run terminates.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0011_agent_run_metadata"
down_revision = "0010_tool_call_ordinal"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def _add(table: str, column: sa.Column) -> None:
    if column.name not in _columns(table):
        op.add_column(table, column)


def upgrade() -> None:
    # Expected tool_call_ids / a2a_invocation_ids — JSON array of
    # strings; mirrors the in-flight work for the actor.
    _add(
        "agent_runs",
        sa.Column("expected_tool_call_ids", sa.JSON(), nullable=True),
    )
    _add(
        "agent_runs",
        sa.Column("expected_a2a_invocation_ids", sa.JSON(), nullable=True),
    )
    # iteration_count — incremented by commit_agent_transition /
    # wait_for_tools; observable on the dashboard / SSE feed.
    _add(
        "agent_runs",
        sa.Column("iteration_count", sa.Integer(), nullable=False, server_default="0"),
    )
    # token_usage — opaque JSON of the last successful LLM attempt.
    _add("agent_runs", sa.Column("token_usage", sa.JSON(), nullable=True))
    # deadline_at — wall-clock cutoff; checked by AgentWorker before
    # claim. The producer sets it via AgentMessage.deadline_at.
    _add("agent_runs", sa.Column("deadline_at", sa.DateTime(), nullable=True))

    if "ix_agent_runs_deadline" not in _indexes("agent_runs"):
        op.create_index(
            "ix_agent_runs_deadline",
            "agent_runs",
            ["deadline_at"],
        )


def downgrade() -> None:
    # Additive-only: indexes dropped, columns left in place so a
    # stale ORM revision never references a missing column.
    try:
        op.drop_index("ix_agent_runs_deadline")
    except Exception:  # noqa: BLE001 — additive-only downgrade
        pass
