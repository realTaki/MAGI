"""rename a2a_invocations.invocation_id → a2a_jobs.job_id and table.

Revision ID: 0003_rename_a2a_invocation_id_and_table
Revises: 0002_drop_run_id_and_rename_event_id
Create Date: 2026-08-10 00:00:00

Companion to the :class:`~magi.bus.guild.sendA2AJob.SendA2AJob`
rename in :mod:`magi.bus.guild.sendA2AJob`.  Brings the A2A
queue's natural-key column (``invocation_id`` → ``job_id``) and
storage table (``a2a_invocations`` → ``a2a_jobs``) in line with
``chat_jobs.job_id`` and ``run_task_jobs.job_id`` so every Job
subclass reads as ``<table>_job_id``.

SQLite's ``ALTER TABLE ... RENAME TO`` automatically follows
indexes and foreign-key references, so the unique index on the
natural-key column (``ix_a2a_invocations_invocation_id``) becomes
``ix_a2a_jobs_job_id`` without an explicit ``DROP INDEX`` /
``CREATE INDEX``.  The intermediate ``RENAME COLUMN`` on
``invocation_id`` → ``job_id`` also renames the unique index to
``ix_a2a_invocations_job_id``; the subsequent table rename finishes
the auto-rename.

Notes
=====

- The renamed column keeps ``UNIQUE NOT NULL`` — same shape as
  ``chat_jobs.job_id`` and ``run_task_jobs.job_id``.
- ``SendA2AResult.job_id`` in the Python dataclass is the same name
  as ``SendA2AJob.job_id``; only the storage spelling changed.
- Downgrade reverses both renames.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_rename_a2a_invocation_id_and_table"
down_revision: str | Sequence[str] | None = "0002_drop_run_id_and_rename_event_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Rename the natural-key column.  SQLite also renames the
    #    unique index ``ix_a2a_invocations_invocation_id`` →
    #    ``ix_a2a_invocations_job_id`` automatically.
    op.execute("ALTER TABLE a2a_invocations RENAME COLUMN invocation_id TO job_id")

    # 2. Rename the table.  SQLite also renames any indexes that
    #    still carry the old table name (the job_id index above
    #    becomes ``ix_a2a_jobs_job_id``).
    op.execute("ALTER TABLE a2a_invocations RENAME TO a2a_jobs")


def downgrade() -> None:
    # Reverse order: table rename first, then column rename.  SQLite
    # follows indexes in both directions.
    op.execute("ALTER TABLE a2a_jobs RENAME TO a2a_invocations")
    op.execute("ALTER TABLE a2a_invocations RENAME COLUMN job_id TO invocation_id")
