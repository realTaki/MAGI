"""Add ``control_jobs`` table — transient BUS-to-worker refresh signal.

The provider worker used to re-read ``runtime_settings.toml`` and
re-construct the LLM SDK client on every claimed job. The 2026-08
refactor caches one provider per process and rebuilds it only when
``save_runtime_settings`` writes a new value. To make the rebuild
durable (and ready for a future multi-replica deploy) the publisher
now inserts a row here; the worker drains it on its next poll tick.

The table is a queue, not an audit log: rows are deleted by the
consumer as part of the drain, and ``hook_evaluations`` /
``llm_attempts`` continue to own the durable trace of what actually
happened. No retention job is needed.

Revision ID: 0006_control_jobs
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0007_control_jobs"
down_revision = "0006_hook_signoffs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "control_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("job_id", name="uq_control_jobs_job_id"),
    )
    op.create_index(
        "ix_control_jobs_drain",
        "control_jobs",
        ["kind", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_control_jobs_drain", table_name="control_jobs")
    op.drop_table("control_jobs")