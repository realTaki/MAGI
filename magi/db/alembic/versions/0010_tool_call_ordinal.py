"""Persist tool-call ordinals so transcript reconstruction is durable.

Revision ID: 0010_tool_call_ordinal
Revises: 0009_idempotency_keys

Design §6.6 of docs/MAGI_single_agent_event_driven_runtime_design.md
requires ``tool_calls.ordinal`` so that, after a crash, the runtime
can rebuild the provider-valid tool_use → tool_result transcript in
the exact order the LLM emitted the tool_calls — independent of the
in-memory ``continuation["tool_call_ids"]`` array.

Today (pre-0010) the order is reconstructed from
``continuation["tool_call_ids"]`` which is serialised into the run's
continuation JSON. That works while a single process owns the run,
but the design's belt-and-suspenders approach wants the ordinal
column on disk so that even after a schema rebuild + LLM attempt
interruption the order survives. The ``load_tool_continuation``
fallback (no ordinal → array order) keeps legacy rows working.

The new index ``ix_tool_calls_run_ordinal`` is read-path only;
``tool_call_id`` is already UNIQUE and ``run_id`` is the natural
filter for the actor worker, so this is a covering index for the
"by-run sorted by ordinal" lookup.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010_tool_call_ordinal"
down_revision = "0009_idempotency_keys"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    # ordinal: nullable so pre-0010 rows survive. New writes populate
    # it monotonically within a run via BusStore.wait_for_tools /
    # enqueue_tool_job. ordered_at is the wall-clock timestamp at
    # which the ordinal was assigned (audit-only).
    if "ordinal" not in _columns("tool_calls"):
        op.add_column("tool_calls", sa.Column("ordinal", sa.Integer(), nullable=True))
    if "ordered_at" not in _columns("tool_calls"):
        op.add_column(
            "tool_calls",
            sa.Column("ordered_at", sa.DateTime(), nullable=True),
        )
    if "ix_tool_calls_run_ordinal" not in _indexes("tool_calls"):
        op.create_index(
            "ix_tool_calls_run_ordinal",
            "tool_calls",
            ["run_id", "ordinal"],
        )


def downgrade() -> None:
    # Additive-only: indexes are dropped, columns left in place to
    # avoid an unsafe ALTER TABLE drop on a hot runtime path.
    try:
        op.drop_index("ix_tool_calls_run_ordinal")
    except Exception:  # noqa: BLE001 — additive-only downgrade
        pass