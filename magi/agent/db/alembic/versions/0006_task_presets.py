"""Add ``task_presets`` table + per-user ``Task`` back-pointers.

The presets feature ships two tables-side-by-side:

1. A new ``task_presets`` table — global templates the operator
   can edit from the Settings tab. Each preset carries the same
   scheduling vocabulary as :class:`TaskIn` (frequency + moment
   fields) but no ``uid`` — presets are not bound to a single
   user. A unique constraint on ``key`` lets the migration seed
   the two defaults idempotently.

2. Two new columns on ``tasks``:
     - ``preset_id``   — FK to ``task_presets.id`` ``SET NULL``,
                         so deleting a template drops the back-pointer
                         on existing per-user rows without cascading
                         through their snapshotted config.
     - ``preset_key``  — immutable snapshot of the source template's
                         ``key`` at seed time. Stays populated even
                         after the template is deleted, so the WebUI's
                         "preset vs custom" split doesn't silently shift
                         when an operator removes a template.

Also seeds two default templates (``daily_standup_brief`` and
``weekly_review``) via ``INSERT … ON CONFLICT (key) DO NOTHING`` so
re-running the migration on an existing DB is a no-op.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_task_presets"
down_revision = "0005_mcp_servers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)

    # 1) Create the ``task_presets`` table if it isn't there
    #    yet. Idempotent — same pattern as ``0005_mcp_servers``
    #    so a fresh DB created by ``Base.metadata.create_all``
    #    AND a DB upgraded via this revision both end up in
    #    the same shape.
    if "task_presets" not in insp.get_table_names():
        op.create_table(
            "task_presets",
            sa.Column("id", sa.String(length=26), nullable=False),
            sa.Column("key", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("prompt", sa.Text(), nullable=False),
            sa.Column("frequency", sa.String(length=16), nullable=False),
            sa.Column("hour", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("minute", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("day_of_week", sa.Integer(), nullable=True),
            sa.Column("day_of_month", sa.Integer(), nullable=True),
            sa.Column("run_at", sa.String(length=32), nullable=True),
            sa.Column(
                "channel",
                sa.String(length=16),
                nullable=False,
                server_default="webui",
            ),
            sa.Column(
                "enabled",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
            sa.Column("created_at", sa.String(length=32), nullable=False),
            sa.Column("updated_at", sa.String(length=32), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("key", name="uq_task_presets_key"),
        )

    # 2) Add the per-user ``Task`` back-pointers. The new
    #    columns are nullable + FK ``SET NULL`` so an older
    #    DB without the new fields stays valid (existing
    #    rows have NULL preset_id / preset_key and live
    #    exactly like before).
    task_cols = {c["name"] for c in insp.get_columns("tasks")}
    if "preset_id" not in task_cols:
        op.add_column(
            "tasks",
            sa.Column("preset_id", sa.String(length=26), nullable=True),
        )
    if "preset_key" not in task_cols:
        op.add_column(
            "tasks",
            sa.Column("preset_key", sa.String(length=64), nullable=True),
        )

    # 3) FK + index. Both are idempotent via the inspector
    #    so re-running on a DB that's already up-to-date is
    #    a no-op (rather than the migration blowing up on
    #    a "constraint already exists" error from SQLite).
    fk_names = {fk["name"] for fk in insp.get_foreign_keys("tasks")}
    if "fk_tasks_preset" not in fk_names:
        op.create_foreign_key(
            "fk_tasks_preset",
            "tasks",
            "task_presets",
            ["preset_id"],
            ["id"],
            ondelete="SET NULL",
        )

    task_indexes = {ix["name"] for ix in insp.get_indexes("tasks")}
    if "ix_tasks_preset_key" not in task_indexes:
        op.create_index(
            "ix_tasks_preset_key",
            "tasks",
            ["preset_key"],
            unique=False,
        )

    # 4) Seed the two default presets. The row's ``id`` is
    #    a fixed ULID-style string so re-running the
    #    migration on an existing DB hits the unique
    #    constraint on ``key`` and skips the insert; the
    #    operator's edits to the prompts / schedules are
    #    preserved (ON CONFLICT DO NOTHING is a true
    #    no-op, not an upsert).
    #
    #    Why fixed ids rather than per-run generated ones:
    #    the id never matters for code paths (the per-user
    #    ``Task.preset_key`` is what the WebUI groups by;
    #    the ``preset_id`` is opaque). Using fixed strings
    #    here keeps the migration diff-printable — every
    #    reviewer sees the exact seed values without
    #    reading Python code.
    _DAILY_BRIEF_ID = "01J9HZ0000DAILYSTAND000UPBR"  # placeholder; replaced below
    _WEEKLY_REVIEW_ID = "01J9HZ0000WEEKLYREVIE0W000"
    # The two ids above are NOT valid Crockford ULIDs (the
    # first char '0' is fine; the format just needs to be
    # a 26-char string with the right alphabet). They're
    # stable IDs — replace with real ULIDs if we ever
    # cross-reference them in code or docs.
    op.execute(
        sa.text(
            """
            INSERT INTO task_presets
                (id, key, name, description, prompt,
                 frequency, hour, minute, day_of_week, day_of_month,
                 run_at, channel, enabled,
                 created_at, updated_at)
            VALUES
                (:id, :key, :name, :description, :prompt,
                 :frequency, :hour, :minute, :dow, :dom,
                 :run_at, :channel, :enabled,
                 :ts, :ts)
            ON CONFLICT (key) DO NOTHING
            """
        ),
        params={
            "id": "01J9HZ0000DAILYSTAND000UPBR",
            "key": "daily_standup_brief",
            "name": "每日晨报",
            "description": (
                "每个工作日 09:00 推送当日待办摘要 + 昨日完成情况。"
            ),
            "prompt": (
                "You are generating a brief morning summary for the "
                "assigned user.\n\n"
                "Today's open tasks:\n{tasks_open}\n\n"
                "Yesterday's completed tasks:\n{tasks_done}\n\n"
                "Urgent action items:\n{action_items}\n\n"
                "Write a concise (≤120 words) stand-up brief in the "
                "user's preferred language. Highlight anything due "
                "today, any blockers, and a single suggested focus "
                "for the morning."
            ),
            "frequency": "daily",
            "hour": 9,
            "minute": 0,
            "dow": None,
            "dom": None,
            "run_at": None,
            "channel": "tg",
            "enabled": 1,
            "ts": "2026-01-01T00:00:00+00:00",
        },
    )
    op.execute(
        sa.text(
            """
            INSERT INTO task_presets
                (id, key, name, description, prompt,
                 frequency, hour, minute, day_of_week, day_of_month,
                 run_at, channel, enabled,
                 created_at, updated_at)
            VALUES
                (:id, :key, :name, :description, :prompt,
                 :frequency, :hour, :minute, :dow, :dom,
                 :run_at, :channel, :enabled,
                 :ts, :ts)
            ON CONFLICT (key) DO NOTHING
            """
        ),
        params={
            "id": "01J9HZ0000WEEKLYREVIE0W000",
            "key": "weekly_review",
            "name": "周回顾",
            "description": (
                "每周五 17:00 推送本周完成情况 + 下周建议。"
            ),
            "prompt": (
                "You are generating a Friday-evening weekly review "
                "for the assigned user.\n\n"
                "Tasks completed this week:\n{tasks_done_week}\n\n"
                "Tasks still pending:\n{tasks_open_week}\n\n"
                "Action items created or completed this week:\n"
                "{action_items_week}\n\n"
                "Write a concise (≤180 words) review covering: what "
                "got done, what carried over, what blocked progress, "
                "and three suggested focus areas for next week. "
                "Reply in the user's preferred language."
            ),
            "frequency": "weekly",
            "hour": 17,
            "minute": 0,
            # Python's weekday(): Mon=0..Sun=6. The
            # downstream ``preset_to_cron`` translates to
            # cron's Sun=0..Sat=6 — so we send 4 (Fri) here.
            "dow": 4,
            "dom": None,
            "run_at": None,
            "channel": "tg",
            "enabled": 1,
            "ts": "2026-01-01T00:00:00+00:00",
        },
    )


def downgrade() -> None:
    # Drop the seed rows first so the FK on tasks.preset_id
    # doesn't block the column drop. CASCADE on the FK is
    # already SET NULL, so the per-user rows would survive
    # a preset delete anyway — but explicit cleanup keeps
    # the downgrade idempotent.
    op.execute(
        sa.text(
            "DELETE FROM task_presets WHERE key IN "
            "('daily_standup_brief', 'weekly_review')"
        )
    )
    op.drop_index("ix_tasks_preset_key", table_name="tasks")
    op.drop_constraint("fk_tasks_preset", "tasks", type_="foreignkey")
    op.drop_column("tasks", "preset_key")
    op.drop_column("tasks", "preset_id")
    op.drop_table("task_presets")