
"""Single-shot baseline — the canonical final schema for MAGI.

The codebase is in dev mode (no production upgrade story).
Every schema change lands here directly and the migration
history collapses to a single ``0001_baseline`` step. A
fresh database created from this revision has the same
shape as a database that's been upgraded through every
follow-on revision (0002..0007) on prior codebase versions.

What this file owns
-------------------

The runtime database has twelve application tables + the
``alembic_version`` bookkeeping row + the FTS5 virtual
table for chat search. The schema below is the
**final** state — anything that arrived via a follow-on
migration (0002..0007) on older codebase versions is folded
in here so a fresh ``alembic upgrade head`` lands on the
same shape.

Tables (in dependency order):

  - chat_sessions          per-user conversation threads
  - contacts               unified person directory
                           (role enum collapsed: ``assigned`` / ``guest``)
  - magic                 MAGIC = internal individual MAGI row name
                           (carries LLM provider + api_key)
  - magis                  MAGIS = MAGI Society = tree of groups
                           (parent_id self-FK + adam_id → magic.id)
  - settings               KV store for system.timezone etc.
  - action_items           dashboard to-do surface
                           (with ``due_date`` and the open-per-kind
                           partial unique index)
  - chat_messages          per-message rows (active + archive)
  - chat_messages_fts      FTS5 index + sync triggers
  - memory_entries         uid-keyed self mid-term memory
  - contact_notes          per-fact notes about a contact
                           (kind='permanent'|'daily', with the
                           partial unique index for daily rows)
  - eve_runtimes           desired/observed EVA lifecycle state
                           (magi_id → magic.id CASCADE)
  - tasks                  per-user scheduled tasks
                           (with preset_id / preset_key back-pointers)
  - task_presets           runtime templates (synced from
                           ``prompts/task_presets/`` after schema init)
  - token_usage            per-contact LLM token aggregation
  - task_runs              one row per task fire (cron or manual)
  - mcp_servers            operator-configured MCP server rows

Why this isn't ``Base.metadata.create_all``
------------------------------------------

The file declares the schema explicitly rather than calling
``create_all`` so future model changes cannot silently
alter a fresh database. ``create_all`` would happily miss
a CHECK constraint or a non-ORM trigger; this file won't.
Existing pre-Alembic databases are adopted by the
runtime's one-time legacy compatibility pass before
Alembic stamps this baseline.
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
    # ### chat_sessions ───────────────────────────────────────────────── #
    op.create_table(
        "chat_sessions",
        sa.Column("session_id", sa.String(length=26), nullable=False),
        sa.Column("delivery_address", sa.String(length=64), nullable=False),
        sa.Column("uid", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=80), nullable=True),
        sa.Column("active_tail_count", sa.Integer(), nullable=False),
        sa.Column("last_compaction_at", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("session_id"),
    )
    with op.batch_alter_table("chat_sessions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_chat_sessions_delivery_address"),
            ["delivery_address"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_chat_sessions_uid"),
            ["uid"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_chat_sessions_updated_at"),
            ["updated_at"],
            unique=False,
        )

    # ### contacts (unified person directory) ─────────────────────────── #
    # Role enum collapsed (post-2024 role/admin split): only
    # ``assigned`` and ``guest`` are reachable from this
    # column. The old ``admin`` / ``contact`` enum values
    # were removed (the former split into ``role='assigned',
    # admin=True``; the latter was functionally identical to
    # ``guest``).
    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("admin", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("separated_at", sa.DateTime(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # Unique on telegram_id for non-NULL rows — one
    # Contact per bound TG chat. Partial index keyed on
    # ``WHERE telegram_id IS NOT NULL`` so multiple
    # unbound Contacts can coexist.
    op.create_index(
        "ux_contacts_telegram_id",
        "contacts",
        ["telegram_id"],
        unique=True,
        sqlite_where=sa.text("telegram_id IS NOT NULL"),
    )

    # ### magis (MAGIS — a MAGI Society; the tree of groups) ─────────── #
    # ``parent_id`` is a self-FK with RESTRICT — the API
    # endpoint re-parents children before deleting a
    # MAGIS, so the DB-level guard is belt-and-braces.
    # ``adam_id`` points at the manager ``magic`` row
    # (the MAGI assigned the reserved ``ADAM`` role for this
    # MAGIS); SET NULL because deleting the ADAM
    # MAGIC shouldn't cascade through the society.
    op.create_table(
        "magis",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("adam_id", sa.Integer(), nullable=True),
        sa.Column("instruction", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["adam_id"], ["magic.id"], ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["magis.id"], ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # ### magic (MAGIC — internal individual MAGI row name) ─────────── #
    # Carries LLM credentials and the MAGI's personal instruction.
    # MAGIS membership / role is modelled separately below.
    op.create_table(
        "magic",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("api_key", sa.String(length=256), nullable=True),
        sa.Column("instruction", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "magis_roles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("magis_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_reserved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["magis_id"], ["magis.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("magis_id", "name", name="uq_magis_roles_magis_name"),
    )
    op.create_index("ix_magis_roles_magis_id", "magis_roles", ["magis_id"])
    op.create_table(
        "magis_memberships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("magis_id", sa.Integer(), nullable=False),
        sa.Column("magic_id", sa.Integer(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["magis_id"], ["magis.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["magic_id"], ["magic.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["magis_roles.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("magic_id", name="uq_magis_memberships_magic"),
    )
    op.create_index("ix_magis_memberships_magis_id", "magis_memberships", ["magis_id"])
    op.create_index("ix_magis_memberships_magic_id", "magis_memberships", ["magic_id"])

    # ### settings (KV) ─────────────────────────────────────────────── #
    op.create_table(
        "settings",
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.String(length=32),
            server_default=sa.text("(datetime('now'))"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )

    # ### action_items (with due_date + open-per-kind unique) ───────── #
    # ``due_date`` landed with the C4 follow-ups; folded
    # into the baseline so a fresh DB has it from day one.
    # The partial unique index ``ux_action_items_open_per_kind``
    # ensures one OPEN row per ``(uid, kind)`` so a
    # dismissed/completed row doesn't block a future
    # same-kind prompt.
    op.create_table(
        "action_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uid", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("target_url", sa.String(length=500), nullable=True),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("completed_by_uid", sa.Integer(), nullable=True),
        sa.Column("completion_note", sa.String(length=500), nullable=True),
        sa.Column("dismissed", sa.Boolean(), nullable=False),
        sa.Column("due_date", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["completed_by_uid"], ["contacts.id"], ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["uid"], ["contacts.id"], ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("action_items", schema=None) as batch_op:
        batch_op.create_index(
            "ix_action_items_uid", ["uid"], unique=False,
        )
        batch_op.create_index(
            "ix_action_items_uid_recent",
            ["uid", sa.text("created_at DESC")],
            unique=False,
        )
    op.create_index(
        "ux_action_items_open_per_kind",
        "action_items",
        ["uid", "kind"],
        unique=True,
        sqlite_where=sa.text("completed_at IS NULL AND dismissed = 0"),
    )

    # ### chat_messages ─────────────────────────────────────────────── #
    # ``ts`` is an ISO UTC string (matches the JSON format
    # the table replaced); not DateTime so we don't need
    # tz-aware handling in code that already passes the
    # string through.
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(length=26), nullable=False),
        sa.Column("message_id", sa.String(length=26), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("ts", sa.String(length=32), nullable=False),
        sa.Column("archived", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"], ["chat_sessions.session_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id", "message_id",
            name="uq_chat_messages_session_msg",
        ),
    )
    with op.batch_alter_table("chat_messages", schema=None) as batch_op:
        batch_op.create_index(
            "ix_chat_messages_session_archived",
            ["session_id", "archived", "id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_chat_messages_session_id"),
            ["session_id"],
            unique=False,
        )

    # ### memory_entries (uid-keyed, post-D.23) ─────────────────────── #
    # ``uid`` is the cross-channel identity key. FK with
    # CASCADE because a row is meaningless without its
    # owner.
    op.create_table(
        "memory_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uid", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("importance", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["uid"], ["contacts.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("memory_entries", schema=None) as batch_op:
        batch_op.create_index(
            "ix_memory_entries_owner_importance",
            ["uid", "completed_at", "importance"],
            unique=False,
        )

    # ### contact_notes (per-fact notes + daily log) ───────────────── #
    # Two kinds live here:
    #   - ``kind='permanent'`` — long-arc facts (``add_contact_note``)
    #   - ``kind='daily'`` — short-arc daily log (``update_daily_note``)
    # The partial unique index ``ux_contact_notes_daily`` ensures one
    # daily row per ``(contact_id, note_date)`` while still letting
    # permanent rows share ``note_date=NULL`` freely.
    op.create_table(
        "contact_notes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="permanent"),
        sa.Column("note_date", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("contact_notes", schema=None) as batch_op:
        batch_op.create_index(
            "ix_contact_notes_contact_id",
            ["contact_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_contact_notes_uid_kind_date",
            ["contact_id", "kind", "note_date"],
            unique=False,
        )
    op.create_index(
        "ux_contact_notes_daily",
        "contact_notes",
        ["contact_id", "note_date"],
        unique=True,
        sqlite_where=sa.text("kind = 'daily'"),
    )

    # ### eve_runtimes (desired/observed EVA lifecycle state) ──────── #
    # ``magic_id`` references ``magic.id`` (the individual
    # MAGIC this EVA runs for) with CASCADE — deleting a
    # MAGIC row also drops its lifecycle row.
    op.create_table(
        "eve_runtimes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("magic_id", sa.Integer(), nullable=False),
        sa.Column("desired_state", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("observed_state", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("namespace", sa.String(length=63), nullable=True),
        sa.Column("deployment_name", sa.String(length=63), nullable=True),
        sa.Column("workspace_claim_name", sa.String(length=63), nullable=True),
        sa.Column("credential_secret_name", sa.String(length=63), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["magic_id"], ["magic.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("magic_id"),
    )
    op.create_index(
        "ix_eve_runtimes_magic_id",
        "eve_runtimes",
        ["magic_id"],
        unique=True,
    )

    # ### task_presets (operator-editable templates) ───────────────── #
    # Synced from YAML files in ``prompts/task_presets/``
    # at boot; the operator can add more from the
    # Settings page. Bundled defaults land via
    # ``sync_bundled_task_presets`` after schema init, not
    # as SQL literals here — keeps the schema declaration
    # free of business content.
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

    # ### tasks (with preset_id / preset_key back-pointers) ─────────── #
    # Both back-pointer columns are declared in the CREATE
    # because SQLite can't ALTER TABLE to ADD a foreign key
    # in place. ``uid`` → contacts.id is RESTRICT — the
    # action_items pattern: a task references its
    # operator's credentials; deleting the operator must
    # require removing tasks first.
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("cron", sa.String(length=120), nullable=False),
        sa.Column("run_at", sa.String(length=32), nullable=True),
        sa.Column("tz", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("delivery_to", sa.String(length=128), nullable=True),
        sa.Column("session_id", sa.String(length=26), nullable=True),
        sa.Column("uid", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("last_run_at", sa.String(length=32), nullable=True),
        sa.Column("last_status", sa.String(length=16), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.Column("preset_id", sa.String(length=26), nullable=True),
        sa.Column("preset_key", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"], ["chat_sessions.session_id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["uid"], ["contacts.id"], ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["preset_id"], ["task_presets.id"], ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_tasks_name"),
    )
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.create_index("ix_tasks_contact", ["uid"], unique=False)
        batch_op.create_index(
            "ix_tasks_enabled_last_run",
            ["enabled", "last_run_at"],
            unique=False,
        )
        batch_op.create_index(
            "ix_tasks_preset_key", ["preset_key"], unique=False,
        )

    # ### token_usage ───────────────────────────────────────────────── #
    op.create_table(
        "token_usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uid", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_creation_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["uid"], ["contacts.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("token_usage", schema=None) as batch_op:
        batch_op.create_index(
            "ix_token_usage_emp_ts", ["uid", "ts"], unique=False,
        )

    # ### task_runs ─────────────────────────────────────────────────── #
    op.create_table(
        "task_runs",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("task_id", sa.String(length=26), nullable=False),
        sa.Column("session_id", sa.String(length=26), nullable=True),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.String(length=32), nullable=False),
        sa.Column("finished_at", sa.String(length=32), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("reply_excerpt", sa.String(length=500), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"], ["chat_sessions.session_id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("task_runs", schema=None) as batch_op:
        batch_op.create_index(
            "ix_task_runs_task_started",
            ["task_id", "started_at"],
            unique=False,
        )

    # ### mcp_servers ───────────────────────────────────────────────── #
    # Operator-configured MCP servers. ``name`` is the
    # primary key and the tool-name prefix surfaced to
    # the LLM (e.g. ``minimax__echo``).
    op.create_table(
        "mcp_servers",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("connection_type", sa.String(length=16), nullable=False),
        sa.Column("command", sa.String(length=256), nullable=True),
        sa.Column("args_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("env_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("url", sa.String(length=512), nullable=True),
        sa.Column("headers_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("connect_timeout", sa.Float(), nullable=True),
        sa.Column("execute_timeout", sa.Float(), nullable=True),
        sa.Column("sse_read_timeout", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )

    # ### FTS5 chat search ─────────────────────────────────────────── #
    # Optional — falls through silently on stripped SQLite
    # builds. Virtual table + sync triggers for full-text
    # search across ``chat_messages``. The DDL list lives
    # in :mod:`magi.db.migrations._FTS_MIGRATIONS`
    # so the source of truth is one module (this file
    # just inlines it).
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
            from magi.bus._persistence.migrations import _FTS_MIGRATIONS
            for _name, ddl in _FTS_MIGRATIONS:
                bind.execute(text(ddl))
            bind.execute(
                text(
                    "INSERT INTO chat_messages_fts(chat_messages_fts) "
                    "VALUES('rebuild')"
                )
            )
        except Exception:
            # FTS is an optional acceleration layer; a
            # stripped SQLite build should still boot.
            # Search route reports unavailable instead.
            pass


def downgrade() -> None:
    # The baseline is an adoption boundary. Do not drop
    # the complete application database as an accidental
    # downgrade.
    pass
