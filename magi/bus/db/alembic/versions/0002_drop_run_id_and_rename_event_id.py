"""drop run_id, rename chat_jobs.event_id → chat_jobs.job_id.

Revision ID: 0002_drop_run_id_and_rename_event_id
Revises: 0001_baseline
Create Date: 2026-08-10 00:00:00

Companion to the ``run_id`` removal + ``event_id`` → ``job_id``
rename in :mod:`magi.bus.guild.chatJob`.  Brings the DB schema in
line with the Python dataclasses so the two stay in sync.

Tables affected:

- ``chat_jobs`` — rename ``event_id`` → ``job_id`` (natural key of
  :class:`~magi.bus.guild.chatJob.chatJobBoard`); drop ``run_id``
  column; drop ``ix_chat_jobs_run_id`` if it still exists.
- ``tool_jobs``, ``delivery_outbox``, ``a2a_jobs``, ``run_task_jobs``,
  ``token_usage``, ``chat_messages`` — drop ``run_id`` column.
- ``ix_a2a_invocations_run_id`` (the original a2a index name;
  SQLite's rename above follows the index to ``ix_a2a_jobs_run_id``
  — both spellings are guarded here), ``ix_chat_messages_run_id`` —
  dropped explicitly so the ``DROP COLUMN`` doesn't trip on a
  still-referenced index.

``task_runs`` has no ``run_id`` column — its primary key is already
named ``id`` — so no DDL is needed for it.

Notes
=====

- SQLite ≥3.35 supports ``ALTER TABLE ... DROP COLUMN``; the project
  already requires a modern Python and SQLite, so this is safe.
- All ``DROP COLUMN`` / ``DROP INDEX`` statements are idempotent in
  spirit — re-running against the post-migration schema is a no-op
  (the ``IF EXISTS`` clause is supported on SQLite indexes; for
  columns we rely on the migration running once per fresh upgrade).
- ``chat_jobs`` carries an explicit unique index on ``event_id``
  that SQLite auto-renames to ``job_id`` during the column rename,
  so no explicit index rebuild is needed.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_drop_run_id_and_rename_event_id"
down_revision: str | Sequence[str] | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Rename the chat_jobs natural-key column.
    op.execute("ALTER TABLE chat_jobs RENAME COLUMN event_id TO job_id")

    # 2. Drop indexes that referenced run_id exclusively.
    #    SQLite's RENAME COLUMN on chat_jobs.event_id follows the
    #    unique index automatically (→ chat_jobs_job_id), but the
    #    run_id indexes on other tables are still around.
    op.execute("DROP INDEX IF EXISTS ix_chat_jobs_run_id")
    op.execute("DROP INDEX IF EXISTS ix_a2a_jobs_run_id")
    op.execute("DROP INDEX IF EXISTS ix_a2a_invocations_run_id")  # pre-0003 spelling
    op.execute("DROP INDEX IF EXISTS ix_chat_messages_run_id")

    # 3. Drop run_id columns from every table that had one.
    op.execute("ALTER TABLE chat_jobs        DROP COLUMN run_id")
    op.execute("ALTER TABLE tool_jobs        DROP COLUMN run_id")
    op.execute("ALTER TABLE delivery_outbox  DROP COLUMN run_id")
    op.execute("ALTER TABLE a2a_jobs         DROP COLUMN run_id")
    op.execute("ALTER TABLE a2a_invocations  DROP COLUMN run_id")  # pre-0003 spelling
    op.execute("ALTER TABLE run_task_jobs    DROP COLUMN run_id")
    op.execute("ALTER TABLE token_usage      DROP COLUMN run_id")
    op.execute("ALTER TABLE chat_messages    DROP COLUMN run_id")


def downgrade() -> None:
    """Reverse: re-add ``run_id``, rename ``job_id`` back to ``event_id``.

    Downgrade is best-effort — pre-rename rows will have their
    ``run_id`` reconstructed as the empty string, which matches the
    pre-rename default (``Mapped(String(64), default="")`` /
    ``Mapped(String(64), nullable=True)`` depending on the table).
    """
    # 1. Re-add run_id columns (matching the pre-rename nullability).
    op.execute("ALTER TABLE chat_jobs        ADD COLUMN run_id VARCHAR(64) NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE tool_jobs        ADD COLUMN run_id VARCHAR(64) DEFAULT ''")
    op.execute("ALTER TABLE delivery_outbox  ADD COLUMN run_id VARCHAR(64) DEFAULT ''")
    op.execute("ALTER TABLE a2a_jobs         ADD COLUMN run_id VARCHAR(64) NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE run_task_jobs    ADD COLUMN run_id VARCHAR(64)")
    op.execute("ALTER TABLE token_usage      ADD COLUMN run_id VARCHAR(64)")
    op.execute("ALTER TABLE chat_messages    ADD COLUMN run_id VARCHAR(64)")

    # 2. Re-create indexes that previously existed.
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_jobs_run_id     ON chat_jobs (run_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_a2a_jobs_run_id      ON a2a_jobs (run_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_messages_run_id ON chat_messages (run_id)")

    # 3. Rename chat_jobs.job_id back to event_id.
    op.execute("ALTER TABLE chat_jobs RENAME COLUMN job_id TO event_id")
