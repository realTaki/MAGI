"""Add ``hook_evaluations`` table for the BUS hook subsystem.

Persists one row per (handler, hook_event_id) pair so:

  - Re-evaluation by the same handler version returns the cached
    decision (idempotency per spec §13).
  - Hook versions can change without losing the prior audit
    trail — the unique constraint includes ``hook_version``.
  - The frontend knowledge page can list recent evaluations per
    subject without re-running handlers.

The unique key is ``(hook_event_id, hook_id, hook_version)`` and
the secondary indexes target the two most common query shapes
(listing by subject, listing by hook_point + status).

Revision ID: 0003_hook_evaluations
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0003_hook_evaluations"
down_revision = "0002_add_llm_attempt_lease"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hook_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("hook_event_id", sa.String(length=64), nullable=False),
        sa.Column("hook_id", sa.String(length=128), nullable=False),
        sa.Column("hook_version", sa.String(length=32), nullable=False),
        sa.Column("hook_point", sa.String(length=64), nullable=False),
        sa.Column("subject_type", sa.String(length=64), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("failure_mode", sa.String(length=16), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("requested_scopes", sa.JSON(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=True),
        sa.Column("reason_code", sa.String(length=96), nullable=True),
        sa.Column("labels", sa.JSON(), nullable=True),
        sa.Column("risk_score", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.String(length=64), nullable=True),
        sa.Column("sanitized_error", sa.String(length=512), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "hook_event_id", "hook_id", "hook_version",
            name="uq_hook_evaluations_event_hook_version",
        ),
    )
    op.create_index(
        "ix_hook_evaluations_subject",
        "hook_evaluations",
        ["subject_type", "subject_id"],
    )
    op.create_index(
        "ix_hook_evaluations_point_status",
        "hook_evaluations",
        ["hook_point", "status"],
    )
    op.create_index(
        "ix_hook_evaluations_created",
        "hook_evaluations",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_hook_evaluations_created", table_name="hook_evaluations")
    op.drop_index("ix_hook_evaluations_point_status", table_name="hook_evaluations")
    op.drop_index("ix_hook_evaluations_subject", table_name="hook_evaluations")
    op.drop_table("hook_evaluations")
