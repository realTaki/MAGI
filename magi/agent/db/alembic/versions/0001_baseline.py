"""Single-shot baseline — the entire MAGI schema, fresh DB only.

This revision is the canonical source of truth for the
MAGI runtime database. The codebase is in dev mode (no
production upgrade story), so every schema change lands
here directly and the migration history collapses to a
single ``0001_baseline`` step.

Schema in this file (in dependency order):

  - chat_sessions        (per-user conversation threads)
  - contacts             (unified person table; ``assigned`` role gets
                          auto-seeded preset tasks)
  - magics               (MAGI team tree)
  - magis                (individual MAGI agents)
  - settings             (KV store for system.timezone + presets)
  - action_items         (with ``due_date`` for C4 EVE follow-ups)
  - chat_messages        (with FTS5 sync triggers — see FTS_MIGRATIONS)
  - memory_entries       (owner keyed by ``uid`` after D.23 rename)
  - tasks                (per-user scheduled tasks; with ``preset_id``
                          FK → task_presets.id and ``preset_key``
                          snapshot for the WebUI's two-list split)
  - task_presets         (operator-editable templates; seeded with
                          four defaults — daily standup brief +
                          weekly review + morning brief + night
                          summary; the last two are the system-
                          level daily-report tasks that read the
                          daily_notes table)
  - token_usage          (per-contact LLM token aggregation)
  - task_runs            (one row per fire — cron or manual)

The file is explicit rather than calling
``Base.metadata.create_all`` so future model changes cannot
silently alter a fresh database created from this revision.
Existing databases are adopted by the runtime's one-time
legacy compatibility pass before Alembic stamps this
baseline.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ### chat_sessions ─────────────────────────────────────────────── #
    op.create_table('chat_sessions',
    sa.Column('session_id', sa.String(length=26), nullable=False),
    sa.Column('delivery_address', sa.String(length=64), nullable=False),
    sa.Column('uid', sa.Integer(), nullable=False),
    sa.Column('channel', sa.String(length=16), nullable=False),
    sa.Column('title', sa.String(length=80), nullable=True),
    sa.Column('active_tail_count', sa.Integer(), nullable=False),
    sa.Column('last_compaction_at', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.Column('updated_at', sa.String(length=32), nullable=False),
    sa.PrimaryKeyConstraint('session_id')
    )
    with op.batch_alter_table('chat_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_chat_sessions_delivery_address'), ['delivery_address'], unique=False)
        batch_op.create_index(batch_op.f('ix_chat_sessions_uid'), ['uid'], unique=False)
        batch_op.create_index(batch_op.f('ix_chat_sessions_updated_at'), ['updated_at'], unique=False)

    # ### contacts ──────────────────────────────────────────────────── #
    op.create_table('contacts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('display_name', sa.String(length=120), nullable=True),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('admin', sa.Boolean(), nullable=False, server_default='0'),
    sa.Column('telegram_id', sa.BigInteger(), nullable=True),
    sa.Column('separated_at', sa.DateTime(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )

    # ### magics ────────────────────────────────────────────────────── #
    op.create_table('magics',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('parent_id', sa.Integer(), nullable=True),
    sa.Column('adam_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['adam_id'], ['magis.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['parent_id'], ['magics.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )

    # ### magis ─────────────────────────────────────────────────────── #
    op.create_table('magis',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=True),
    sa.Column('magic_id', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(length=64), nullable=True),
    sa.Column('api_key', sa.String(length=256), nullable=True),
    sa.Column('magic_position', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['magic_id'], ['magics.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )

    # ### settings (KV) ─────────────────────────────────────────────── #
    op.create_table('settings',
    sa.Column('key', sa.String(length=255), nullable=False),
    sa.Column('value', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.String(length=32), server_default=sa.text("(datetime('now'))"), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )

    # ### action_items (with due_date) ──────────────────────────────── #
    # ``due_date`` was added later (was 0004_action_items_due_date);
    # folded into the baseline so a fresh DB has it from day one.
    op.create_table('action_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('uid', sa.Integer(), nullable=True),
    sa.Column('kind', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('description', sa.String(length=1000), nullable=True),
    sa.Column('target_url', sa.String(length=500), nullable=True),
    sa.Column('priority', sa.String(length=16), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.Column('completed_by_uid', sa.Integer(), nullable=True),
    sa.Column('completion_note', sa.String(length=500), nullable=True),
    sa.Column('dismissed', sa.Boolean(), nullable=False),
    sa.Column('due_date', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['completed_by_uid'], ['contacts.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['uid'], ['contacts.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )

    # ### chat_messages ─────────────────────────────────────────────── #
    op.create_table('chat_messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('session_id', sa.String(length=26), nullable=False),
    sa.Column('message_id', sa.String(length=26), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('ts', sa.String(length=32), nullable=False),
    sa.Column('archived', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['chat_sessions.session_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('session_id', 'message_id', name='uq_chat_messages_session_msg')
    )
    with op.batch_alter_table('chat_messages', schema=None) as batch_op:
        batch_op.create_index('ix_chat_messages_session_archived', ['session_id', 'archived', 'id'], unique=False)
        batch_op.create_index(batch_op.f('ix_chat_messages_session_id'), ['session_id'], unique=False)

    # ### memory_entries (uid-keyed, post-D.23) ─────────────────────── #
    # The D.23 migration renamed ``employee_id`` → ``uid``; in the
    # collapsed baseline we just use ``uid`` from day one.
    op.create_table('memory_entries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('uid', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('subject', sa.String(length=200), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('importance', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['uid'], ['contacts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('memory_entries', schema=None) as batch_op:
        batch_op.create_index('ix_memory_entries_owner_importance', ['uid', 'completed_at', 'importance'], unique=False)

    # ### task_presets (operator-editable templates) ───────────────── #
    # Replaces the standalone 0006_task_presets migration. New
    # operators find the two defaults (daily standup + weekly
    # review) immediately on first boot — no separate upgrade
    # round needed.
    op.create_table(
        'task_presets',
        sa.Column('id', sa.String(length=26), nullable=False),
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('prompt', sa.Text(), nullable=False),
        sa.Column('frequency', sa.String(length=16), nullable=False),
        sa.Column('hour', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('minute', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('day_of_week', sa.Integer(), nullable=True),
        sa.Column('day_of_month', sa.Integer(), nullable=True),
        sa.Column('run_at', sa.String(length=32), nullable=True),
        sa.Column(
            'channel',
            sa.String(length=16),
            nullable=False,
            server_default='webui',
        ),
        sa.Column(
            'enabled',
            sa.Integer(),
            nullable=False,
            server_default='1',
        ),
        sa.Column('created_at', sa.String(length=32), nullable=False),
        sa.Column('updated_at', sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key', name='uq_task_presets_key'),
    )

    # ### tasks (with preset_id / preset_key back-pointers) ─────────── #
    # Back-pointers were 0006_task_presets — folded here so the
    # per-user ``tasks`` table and the templates table are born
    # together. SQLite can't ALTER TABLE to ADD a foreign key
    # in place, so we declare both inside the table's CREATE.
    op.create_table('tasks',
    sa.Column('id', sa.String(length=26), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('prompt', sa.Text(), nullable=False),
    sa.Column('cron', sa.String(length=120), nullable=False),
    sa.Column('run_at', sa.String(length=32), nullable=True),
    sa.Column('tz', sa.String(length=64), nullable=False),
    sa.Column('channel', sa.String(length=16), nullable=False),
    sa.Column('delivery_to', sa.String(length=128), nullable=True),
    sa.Column('session_id', sa.String(length=26), nullable=True),
    sa.Column('uid', sa.Integer(), nullable=False),
    sa.Column('enabled', sa.Integer(), nullable=False),
    sa.Column('consecutive_failures', sa.Integer(), nullable=False),
    sa.Column('last_run_at', sa.String(length=32), nullable=True),
    sa.Column('last_status', sa.String(length=16), nullable=True),
    sa.Column('last_error', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.Column('updated_at', sa.String(length=32), nullable=False),
    sa.Column('preset_id', sa.String(length=26), nullable=True),
    sa.Column('preset_key', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['session_id'], ['chat_sessions.session_id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['uid'], ['contacts.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['preset_id'], ['task_presets.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name', name='uq_tasks_name')
    )
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.create_index('ix_tasks_contact', ['uid'], unique=False)
        batch_op.create_index('ix_tasks_enabled_last_run', ['enabled', 'last_run_at'], unique=False)
        batch_op.create_index('ix_tasks_preset_key', ['preset_key'], unique=False)

    # ### token_usage ───────────────────────────────────────────────── #
    op.create_table('token_usage',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('uid', sa.Integer(), nullable=False),
    sa.Column('channel', sa.String(length=16), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('model', sa.String(length=64), nullable=True),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('cache_creation_tokens', sa.Integer(), nullable=False),
    sa.Column('cache_read_tokens', sa.Integer(), nullable=False),
    sa.Column('ts', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['uid'], ['contacts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('token_usage', schema=None) as batch_op:
        batch_op.create_index('ix_token_usage_emp_ts', ['uid', 'ts'], unique=False)

    # ### task_runs ─────────────────────────────────────────────────── #
    op.create_table('task_runs',
    sa.Column('id', sa.String(length=26), nullable=False),
    sa.Column('task_id', sa.String(length=26), nullable=False),
    sa.Column('session_id', sa.String(length=26), nullable=True),
    sa.Column('trigger', sa.String(length=16), nullable=False),
    sa.Column('started_at', sa.String(length=32), nullable=False),
    sa.Column('finished_at', sa.String(length=32), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('error', sa.String(length=500), nullable=True),
    sa.Column('reply_excerpt', sa.String(length=500), nullable=True),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['chat_sessions.session_id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('task_runs', schema=None) as batch_op:
        batch_op.create_index('ix_task_runs_task_started', ['task_id', 'started_at'], unique=False)

    # ### mcp_servers (was 0005_mcp_servers) ────────────────────────── #
    # Idempotent guard skipped here because we're on a fresh DB —
    # the baseline's CREATE TABLE always runs cleanly. The
    # original 0005 carried a ``if table exists, skip`` guard
    # because it was a follow-on upgrade; here it's not needed.
    op.create_table(
        'mcp_servers',
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('connection_type', sa.String(length=16), nullable=False),
        sa.Column('command', sa.String(length=256), nullable=True),
        sa.Column('args_json', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('env_json', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('url', sa.String(length=512), nullable=True),
        sa.Column('headers_json', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('connect_timeout', sa.Float(), nullable=True),
        sa.Column('execute_timeout', sa.Float(), nullable=True),
        sa.Column('sse_read_timeout', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('name'),
    )

    # ### FTS5 chat search (was 0002_fts5) ─────────────────────────── #
    # Optional — falls through silently on stripped SQLite builds.
    # Triggers + virtual table for full-text search across
    # ``chat_messages``. The DDL list lives in
    # ``magi/agent/db/migrations._FTS_MIGRATIONS`` so the source
    # of truth is one module (this file just inlines it).
    bind = op.get_bind()
    try:
        has_fts5 = (
            bind.execute(
                text(
                    "SELECT 1 FROM pragma_compile_options "
                    "WHERE compile_options = 'ENABLE_FTS5'"
                )
            ).first()
            is not None
        )
    except Exception:
        has_fts5 = False

    if has_fts5:
        try:
            from magi.agent.db.migrations import _FTS_MIGRATIONS
            for _name, ddl in _FTS_MIGRATIONS:
                bind.execute(text(ddl))
            bind.execute(
                text(
                    "INSERT INTO chat_messages_fts(chat_messages_fts) "
                    "VALUES('rebuild')"
                )
            )
        except Exception:
            # FTS is an optional acceleration layer; a stripped
            # SQLite build should still boot. Search route
            # reports unavailable instead. Recording the migration
            # as ``head`` would only re-fail on every restart.
            pass

    # ### Seed defaults (task_presets) ──────────────────────────────── #
    # The two presets that ``magi/agent/proactive/presets.py``
    # iterates on every assigned-user creation. ``ON CONFLICT
    # (key) DO NOTHING`` keeps the seed idempotent so re-running
    # this revision on a DB that already has the rows is a no-op.
    op.execute(
        sa.text(
            """
            INSERT INTO task_presets
                (id, key, name, description, prompt,
                 frequency, hour, minute, day_of_week, day_of_month,
                 run_at, channel, enabled,
                 created_at, updated_at)
            VALUES
                ('01J9HZ0000DAILYSTAND000UPBR',
                 'daily_standup_brief',
                 '每日晨报',
                 '每个工作日 09:00 推送当日待办摘要 + 昨日完成情况。',
                 'You are generating a brief morning summary for the assigned user. Today''s open tasks: {tasks_open}. Yesterday''s completed tasks: {tasks_done}. Urgent action items: {action_items}. Write a concise (under 120 words) stand-up brief in the user''s preferred language. Highlight anything due today, any blockers, and a single suggested focus for the morning.',
                 'daily', 9, 0, NULL, NULL, NULL, 'tg', 1,
                 '2026-01-01T00:00:00+00:00',
                 '2026-01-01T00:00:00+00:00'),
                ('01J9HZ0000WEEKLYREVIE0W000',
                 'weekly_review',
                 '周回顾',
                 '每周五 17:00 推送本周完成情况 + 下周建议。',
                 'You are generating a Friday-evening weekly review for the assigned user. Tasks completed this week: {tasks_done_week}. Tasks still pending: {tasks_open_week}. Action items created or completed this week: {action_items_week}. Write a concise (under 180 words) review covering: what got done, what carried over, what blocked progress, and three suggested focus areas for next week. Reply in the user''s preferred language.',
                 'weekly', 17, 0, 4, NULL, NULL, 'tg', 1,
                 '2026-01-01T00:00:00+00:00',
                 '2026-01-01T00:00:00+00:00'),
                -- System-level daily report — auto-seeded per
                -- assigned user via ``seed_presets_for_contact``.
                -- Reads today's daily note + mock emails +
                -- mock meetings, sends a TG summary.
                ('01J9HZ0000MORNINGBRIE0B0',
                 'morning_brief',
                 '早报',
                 '每个工作日 08:00 推送当日邮件高光 + 今日行程 + 待办提醒。',
                 '你正在生成早报。按以下顺序拉数据：(1) 调用 read_recent_emails(hours=24) 拉取过去 24h 邮件；(2) 调用 read_upcoming_meetings(days=1) 拿今日日程；(3) 用 search_contacts 或 read_daily_note 看相关人物的最新备注和今天积累的 daily_note。最后按三段结构输出：邮件高光 / 今日行程 / 待办提醒。语气如同事在群里发消息——简洁、直接，避免"很荣幸为你服务"之类的套话。优先使用中文。',
                 'daily', 8, 0, NULL, NULL, NULL, 'tg', 1,
                 '2026-01-01T00:00:00+00:00',
                 '2026-01-01T00:00:00+00:00'),
                ('01J9HZ0000NIGHTSUMMAR0Y0',
                 'night_summary',
                 '晚报',
                 '每天 22:00 推送当日完成情况 + 明早首个会议。',
                 '你正在生成晚报。按以下顺序拉数据：(1) 调用 read_recent_emails(hours=24) 看下午 / 晚上的邮件；(2) 调用 read_upcoming_meetings(days=2) 看今晚 + 明早会议；(3) 读今天的 daily_note 总结今天做完了什么。最后按三段结构输出：今日完成 / 明日首会 / 待办提醒。语气如同事在群里发消息——简洁、直接。优先使用中文。',
                 'daily', 22, 0, NULL, NULL, NULL, 'tg', 1,
                 '2026-01-01T00:00:00+00:00',
                 '2026-01-01T00:00:00+00:00')
            ON CONFLICT (key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    # The baseline is an adoption boundary. Do not drop the complete
    # application database as an accidental downgrade.
    pass