"""Add ``hook_signoffs`` table — pending async plugin acknowledgements.

The OLD hook subsystem (HookService + HookEnvelope + GATE/OBSERVE
synchronous handlers) is replaced by a tag-based design:

  - When a durable row is committed by a bus.store boundary method
    (``enqueue_llm_job`` / ``complete_llm_attempt`` /
    ``enqueue_tool_job`` / ``complete_tool_job`` /
    ``enqueue_delivery`` / ``complete_delivery``), the store
    queries the persistent ``hook_plugin_configs`` table for
    plugins that subscribe to the matching hook point and
    inserts one ``hook_signoffs`` row per (subject, plugin)
    pair with ``pending=1``.

  - Downstream workers (provider / tool / delivery) refuse to
    claim jobs that still have a pending signoff for the same
    subject_type; the WHERE clause ``NOT EXISTS (...)`` keeps
    the row invisible until the plugin acks.

  - Each plugin's worker calls ``claim_pending_signoffs`` to
    pull its own pending rows, processes the related job, and
    then calls ``ack_signoff`` to mark the row signoff-done.
    When the last pending signoff clears, the downstream worker
    can claim the job.

  - ``hook_evaluations`` (the OLD inline hook audit table) and
    the 0003 migration are left in place for downgrade safety;
    no new code reads them.  The next migration (0007) will
    drop them.  Likewise ``hook_plugin_configs`` keeps the
    columns from 0004 -- they are still the source of
    enablement -- but the scope / mode / priority / timeout
    columns lose their meaning now that hooks are async
    observers.  A follow-up migration can trim those columns.

Revision ID: 0006_hook_signoffs
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0006_hook_signoffs"
down_revision = "0005_magic_name_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hook_signoffs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column("hook_point", sa.String(length=64), nullable=False),
        sa.Column("plugin_id", sa.String(length=128), nullable=False),
        sa.Column("pending", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("acked_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "subject_type", "subject_id", "hook_point", "plugin_id",
            name="uq_hook_signoffs_subject_plugin",
        ),
    )
    op.create_index(
        "ix_hook_signoffs_pending",
        "hook_signoffs",
        ["plugin_id", "pending", "created_at"],
    )
    op.create_index(
        "ix_hook_signoffs_subject",
        "hook_signoffs",
        ["subject_type", "subject_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_hook_signoffs_subject", table_name="hook_signoffs")
    op.drop_index("ix_hook_signoffs_pending", table_name="hook_signoffs")
    op.drop_table("hook_signoffs")
