"""Collapse ``run_task_jobs.fired_by`` and ``task_runs.trigger`` to a single ``manual`` bool.

Revision ID: 0013_replace_run_task_fired_by_and_task_run_trigger
Revises: 0012_drop_tasks_key

The Python dataclasses now carry a single ``manual: bool`` flag
(:class:`~magi.bus.guild.runTaskJob.RunTaskJob` /
:class:`~magi.bus.library.local.tasksBook.TaskRun`) in place of the
old string ``trigger`` (5-value closed set) and the redundant
``run_task_jobs.fired_by`` column that overlapped with the existing
``run_task_jobs.manual``. Two-state bool is enough for the real
trigger semantics:

  - True  — 用户/工具主动触发（API / UI / tool）
  - False — task 模块按 cron / run_at 规则自触发

Data migration
==============

``task_runs.trigger`` values map as follows (set ``manual`` then
drop ``trigger``):

  - ``'manual_run'`` / ``'api_manual_run'`` / ``'schedule_task_tool'``
    / ``'manual'`` → 1 (was user-initiated)
  - ``'cron_tick'`` / ``'run_at_consume'`` → 0 (was system-initiated)
  - any unknown value → 0 (safe default; surfaces as "scheduled"
    which is what cron-style fallback already meant)

``run_task_jobs.fired_by`` carried no extra signal beyond the
already-existing ``manual`` column, so its rows are dropped without
a data migration — the ``manual`` column was the source of truth
the whole time.

Idempotency
===========

Every DDL statement is guarded by an inspection of the live schema.
A fresh DB that ran ``create_all`` after the Python ORM was updated
already produces the post-migration shape; re-running against that
DB is a no-op.

Tables affected:

- ``run_task_jobs`` — drop ``fired_by`` column.
- ``task_runs``      — add ``manual`` column, copy semantics from
  ``trigger``, drop ``trigger``.

Indexes:

- ``task_runs`` has no index on ``trigger``, and the new ``manual``
  column doesn't need a new index (it's a bookkeeping field, not a
  query path), so no index DDL is needed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_replace_run_task_fired_by_and_task_run_trigger"
down_revision: str | Sequence[str] | None = "0012_drop_tasks_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_USER_INITIATED_TRIGGERS = frozenset(
    {"manual_run", "api_manual_run", "schedule_task_tool", "manual"}
)


def _has_column(conn: sa.engine.Connection, table: str, column: str) -> bool:
    insp = sa.inspect(conn)
    if table not in insp.get_table_names():
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    conn = op.get_bind()

    # 1. ``run_task_jobs``: drop the redundant ``fired_by`` column.
    #    ``manual`` (Integer, default 1, NOT NULL) already exists and
    #    was the actual source of truth for "user-initiated" here.
    if _has_column(conn, "run_task_jobs", "fired_by"):
        with op.batch_alter_table("run_task_jobs") as batch:
            batch.drop_column("fired_by")

    # 2. ``task_runs``: collapse ``trigger: String(16)`` to ``manual: bool``.
    #    ``manual`` may already be present (fresh DB after create_all) —
    #    only do the rename + data copy when ``trigger`` is still around.
    if _has_column(conn, "task_runs", "trigger") and not _has_column(
        conn, "task_runs", "manual"
    ):
        # 2a. Add ``manual`` as nullable first so the ALTER succeeds
        #     against existing rows; backfill in 2b; tighten to NOT
        #     NULL in 2c after every row has a deterministic value.
        op.execute("ALTER TABLE task_runs ADD COLUMN manual INTEGER")
        # 2b. Backfill: the closed set's user-initiated values become 1,
        #     everything else (cron_tick / run_at_consume / unknown) is 0.
        for value in _USER_INITIATED_TRIGGERS:
            op.execute(
                "UPDATE task_runs SET manual = 1 WHERE trigger = :trigger",
                params={"trigger": value},
            )
        op.execute(
            "UPDATE task_runs SET manual = 0 "
            "WHERE manual IS NULL AND trigger IS NOT NULL"
        )
        # 2c. Tighten the schema: NOT NULL with default 0 so future
        #     INSERTs that omit the column get the safe "scheduled"
        #     fallback rather than NULL.
        with op.batch_alter_table("task_runs") as batch:
            batch.alter_column(
                "manual",
                existing_type=sa.Integer(),
                nullable=False,
                server_default="0",
            )
        # 2d. Drop the now-redundant ``trigger`` column.
        with op.batch_alter_table("task_runs") as batch:
            batch.drop_column("trigger")


def downgrade() -> None:
    """Reverse: re-add ``fired_by`` and ``trigger``, set a best-effort string back.

    Pre-existing ``manual=True`` rows are restored as
    ``'api_manual_run'`` (the most common manual trigger path) and
    ``manual=False`` rows become ``'cron_tick'`` — round-tripping
    is approximate but preserves the dominant signal.
    """
    conn = op.get_bind()

    # 1. ``run_task_jobs``: re-add ``fired_by``.
    if not _has_column(conn, "run_task_jobs", "fired_by"):
        op.execute(
            "ALTER TABLE run_task_jobs ADD COLUMN fired_by VARCHAR(32)"
        )
        op.execute(
            "UPDATE run_task_jobs "
            "SET fired_by = CASE WHEN manual = 1 "
            "THEN 'api_manual_run' ELSE 'cron_tick' END "
            "WHERE fired_by IS NULL"
        )

    # 2. ``task_runs``: re-add ``trigger`` if absent.
    if _has_column(conn, "task_runs", "manual") and not _has_column(
        conn, "task_runs", "trigger"
    ):
        op.execute(
            "ALTER TABLE task_runs ADD COLUMN trigger VARCHAR(16)"
        )
        op.execute(
            "UPDATE task_runs "
            "SET trigger = CASE WHEN manual = 1 "
            "THEN 'api_manual_run' ELSE 'cron_tick' END "
            "WHERE trigger IS NULL"
        )
        with op.batch_alter_table("task_runs") as batch:
            batch.alter_column(
                "trigger",
                existing_type=sa.String(length=16),
                nullable=False,
            )
        # Leave ``manual`` in place — it was always the source of
        # truth, and dropping it now would risk losing data on a
        # downgrade path that was already best-effort.
