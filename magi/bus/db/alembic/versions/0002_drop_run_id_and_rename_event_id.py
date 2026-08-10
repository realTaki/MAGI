"""drop run_id, rename chat_jobs.event_id → chat_jobs.job_id.

Revision ID: 0002_drop_run_id_and_rename_event_id
Revises: 0001_baseline
Create Date: 2026-08-10 00:00:00

Companion to the ``run_id`` removal + ``event_id`` → ``job_id``
rename in :mod:`magi.bus.guild.chatJob`.  Brings the DB schema in
line with the Python dataclasses so the two stay in sync.

Idempotency
===========

Every DDL statement is guarded by an inspection of the live schema
— re-running against an already-current DB is a no-op.  This matters
because :func:`magi.bus.db.schema.upgrade_schema` runs on every
boot, and :func:`apply_initial_schema` runs ``create_all`` first
(which already produces the post-migration shape on a fresh DB).
Without these guards, a fresh DB would hit ``no such column:
event_id`` on the very first boot.

Tables affected:

- ``chat_jobs`` — rename ``event_id`` → ``job_id`` (natural key of
  :class:`~magi.bus.guild.chatJob.chatJobBoard`); drop ``run_id``.
- ``tool_jobs``, ``delivery_outbox``, ``a2a_jobs``, ``run_task_jobs``,
  ``token_usage``, ``chat_messages`` — drop ``run_id`` column.
- ``ix_a2a_jobs_run_id`` (post-0003 spelling) and
  ``ix_a2a_invocations_run_id`` (pre-0003 spelling) — dropped
  explicitly so ``DROP COLUMN`` doesn't trip on a still-referenced
  index.

``task_runs`` has no ``run_id`` column — its primary key is already
named ``id`` — so no DDL is needed for it.

Notes
=====

- SQLite ≥3.35 supports ``ALTER TABLE ... DROP COLUMN``; the project
  already requires a modern Python and SQLite, so this is safe.
- ``ALTER TABLE ... RENAME COLUMN`` automatically follows indexes
  on the renamed column, so the unique index on ``chat_jobs.event_id``
  becomes ``chat_jobs_job_id`` without an explicit rebuild.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_drop_run_id_and_rename_event_id"
down_revision: str | Sequence[str] | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(conn: sa.engine.Connection, table: str, column: str) -> bool:
    insp = sa.inspect(conn)
    return any(c["name"] == column for c in insp.get_columns(table))


def _has_index(conn: sa.engine.Connection, table: str, index_name: str) -> bool:
    insp = sa.inspect(conn)
    return any(idx["name"] == index_name for idx in insp.get_indexes(table))


def upgrade() -> None:
    conn = op.get_bind()

    # 1. chat_jobs: rename event_id → job_id (only if the old name is
    #    still present — fresh DBs already have job_id from create_all).
    if _has_column(conn, "chat_jobs", "event_id"):
        op.execute("ALTER TABLE chat_jobs RENAME COLUMN event_id TO job_id")

    # 2. Drop indexes that referenced run_id exclusively (both the
    #    post-rename ``*_jobs_run_id`` spelling and the pre-rename
    #    ``ix_a2a_invocations_run_id`` spelling).
    for table in ("chat_jobs", "chat_messages"):
        if _has_index(conn, table, f"ix_{table}_run_id"):
            op.execute(f"DROP INDEX IF EXISTS ix_{table}_run_id")
    if _has_index(conn, "a2a_jobs", "ix_a2a_jobs_run_id"):
        op.execute("DROP INDEX IF EXISTS ix_a2a_jobs_run_id")
    if _has_index(conn, "a2a_invocations", "ix_a2a_invocations_run_id"):
        op.execute("DROP INDEX IF EXISTS ix_a2a_invocations_run_id")

    # 3. Drop run_id columns from every table that had one.  Each
    #    DROP is guarded so re-runs against the post-migration schema
    #    are no-ops.
    for table in (
        "chat_jobs",
        "tool_jobs",
        "delivery_outbox",
        "a2a_jobs",
        "a2a_invocations",  # pre-0003 spelling
        "run_task_jobs",
        "token_usage",
        "chat_messages",
    ):
        if _has_column(conn, table, "run_id"):
            op.execute(f"ALTER TABLE {table} DROP COLUMN run_id")


def downgrade() -> None:
    """Reverse: re-add ``run_id``, rename ``job_id`` back to ``event_id``.

    Downgrade is best-effort — pre-rename rows will have their
    ``run_id`` reconstructed as the empty string, which matches the
    pre-rename default (``Mapped(String(64), default="")`` /
    ``Mapped(String(64), nullable=True)`` depending on the table).
    """
    conn = op.get_bind()

    # 1. chat_jobs: rename job_id back to event_id (only if needed).
    if _has_column(conn, "chat_jobs", "job_id") and not _has_column(conn, "chat_jobs", "event_id"):
        op.execute("ALTER TABLE chat_jobs RENAME COLUMN job_id TO event_id")

    # 2. Re-add run_id columns.
    for table, default in (
        ("chat_jobs", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("tool_jobs", "VARCHAR(64) DEFAULT ''"),
        ("delivery_outbox", "VARCHAR(64) DEFAULT ''"),
        ("a2a_jobs", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("run_task_jobs", "VARCHAR(64)"),
        ("token_usage", "VARCHAR(64)"),
        ("chat_messages", "VARCHAR(64)"),
    ):
        if not _has_column(conn, table, "run_id"):
            op.execute(f"ALTER TABLE {table} ADD COLUMN run_id {default}")

    # 3. Re-create indexes.
    for table in ("chat_jobs", "chat_messages", "a2a_jobs"):
        if not _has_index(conn, table, f"ix_{table}_run_id"):
            op.execute(f"CREATE INDEX IF NOT EXISTS ix_{table}_run_id ON {table} (run_id)")
