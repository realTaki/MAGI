"""Single-shot initial schema for MAGI's runtime SQLite database.

Revision ID: 0001_initial_schema

This is the **only** alembic revision. The 2026.08 dev-mode
collapse folded the previous 15-revision chain (0001-0015) into
one baseline so every dev install starts with the final schema
and Alembic's ``upgrade head`` is a single transaction.

Every table here is owned by one of three Base subclasses:

  - ``Base`` from :mod:`magi.bus.db.base` — runtime database
    tables (sessions, contacts, tasks, bus queue, ...).
  - The same ``Base`` covers the public PostgreSQL schema; in
    production that schema is built by
    :mod:`magi.bus.db.magis` via ``Base.metadata.create_all``
    (the PG Alembic port is a known deferred item).

Schema source of truth: the ORM models in :mod:`magi.bus.models`.
This migration's CREATE TABLE statements are hand-mirrored from
those models; any divergence is a bug. ``env.py`` imports every
model so ``alembic revision --autogenerate`` (the dev workflow)
can compare against this baseline.

Tables owned (alphabetical)
--------------------------

  - ``a2a_invocations``      — peer-MAGI call lifecycle.
  - ``action_items``         — dashboard to-do inbox.
  - ``agent_inbox``          — durable inbox of turn requests.
  - ``agent_runs``           — one row per agent turn.
  - ``auth_credentials``     — per-UID login secrets.
  - ``chat_messages``        — chat transcript rows.
  - ``chat_messages_fts``    — FTS5 virtual table over chat_messages.
  - ``chat_sessions``        — chat session header.
  - ``contact_notes``        — long-arc facts about people.
  - ``contacts``             — unified people directory.
  - ``control_operators``    — singleton WebUI control-plane admins.
  - ``control_settings``     — singleton WebUI control-plane KV.
  - ``deliveries``           — durable committed channel delivery
                               outbox (BusStore.DeliveryOutbox).
  - ``eve_runtimes``         — desired/observed state for EVA
                               Kubernetes Deployments.
  - ``magic``                — individual MAGI agent rows.
  - ``magis``                — MAGI Society tree.
  - ``magis_admins``         — direct MAGIS administrators.
  - ``magis_memberships``    — MAGIC's direct MAGIS home.
  - ``magis_roles``          — role rows per MAGIS (ADAM/EVA + custom).
  - ``mcp_servers``          — operator-configured MCP server rows.
  - ``memory_entries``       — MAGI's long-term self memory.
  - ``meta``                 — legacy raw-SQL KV bootstrap.
  - ``run_inputs``           — ordered input events within a run.
  - ``settings``             — runtime KV (timezone, tool-iter, ...).
  - ``task_presets``         — proactive task templates.
  - ``task_runs``            — per-fire execution audit.
  - ``tasks``                — operator-defined scheduled tasks.
  - ``token_usage``          — per-outbound-LLM-call billing rows.
  - ``tool_catalog_state``   — singleton monotonic catalog
                               revision + snapshot hash.
  - ``tool_calls``           — within-run tool call record + ordinal.
  - ``tool_definitions``     — durable Tool Catalog rows.
  - ``tool_jobs``            — durable tool execution jobs.
  - ``llm_attempts``         — per-inference lifecycle.

The legacy ``alembic_version`` bookkeeping row is created by
Alembic itself (``command.stamp`` after this migration runs).
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ============================================================== #
    # Private SQLite database — MAGI runtime tables.
    # ============================================================== #

    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=120)),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="guest"),
        sa.Column("admin", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("telegram_id", sa.BigInteger()),
        sa.Column("separated_at", sa.DateTime()),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("telegram_id", name="uq_contacts_telegram_id"),
    )

    op.create_table(
        "contact_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("contact_id", sa.Integer(),
                  sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="eve"),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="permanent"),
        sa.Column("note_date", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_contact_notes_contact_id", "contact_notes", ["contact_id"])

    op.create_table(
        "memory_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uid", sa.Integer(),
                  sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("importance", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="eve"),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_memory_entries_owner_importance",
        "memory_entries",
        ["uid", "completed_at", "importance"],
    )

    op.create_table(
        "settings",
        sa.Column("key", sa.String(length=255), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False,
                  server_default=sa.text("(datetime('now'))")),
    )

    op.create_table(
        "token_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uid", sa.Integer(),
                  sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64)),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_creation_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ts", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_token_usage_emp_ts", "token_usage", ["uid", "ts"])

    op.create_table(
        "auth_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uid", sa.Integer(),
                  sa.ForeignKey("contacts.id"), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("secret_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_unique_constraint(
        "ux_auth_credentials_uid_kind", "auth_credentials", ["uid", "kind"]
    )
    op.create_index("ix_auth_credentials_uid", "auth_credentials", ["uid"])

    op.create_table(
        "action_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uid", sa.Integer(),
                  sa.ForeignKey("contacts.id", ondelete="SET NULL")),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=1000)),
        sa.Column("target_url", sa.String(length=500)),
        sa.Column("priority", sa.String(length=16), nullable=False, server_default="normal"),
        sa.Column("due_date", sa.DateTime()),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("completed_by_uid", sa.Integer(),
                  sa.ForeignKey("contacts.id", ondelete="SET NULL")),
        sa.Column("completion_note", sa.String(length=500)),
        sa.Column("dismissed", sa.Boolean(), nullable=False, server_default="0"),
    )

    op.create_table(
        "chat_sessions",
        sa.Column("session_id", sa.String(length=26), primary_key=True),
        sa.Column("delivery_address", sa.String(length=64), nullable=False, index=True),
        sa.Column("uid", sa.Integer(), nullable=False, index=True),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=80)),
        sa.Column("active_tail_count", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("last_compaction_at", sa.String(length=32)),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False, index=True),
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.String(length=26),
                  sa.ForeignKey("chat_sessions.session_id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("message_id", sa.String(length=26), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("ts", sa.String(length=32), nullable=False),
        sa.Column("archived", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_blocks", sa.JSON()),
        sa.Column("run_id", sa.String(length=64), index=True),
        sa.Column("llm_attempt_id", sa.String(length=128)),
    )
    op.create_unique_constraint(
        "uq_chat_messages_session_msg", "chat_messages", ["session_id", "message_id"]
    )
    op.create_index(
        "ix_chat_messages_session_archived",
        "chat_messages",
        ["session_id", "archived", "id"],
    )

    # FTS5 external-content virtual table mirroring chat_messages.text.
    # CREATE VIRTUAL TABLE IF NOT EXISTS is idempotent in SQLite.
    op.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS chat_messages_fts USING fts5(
            text,
            content='chat_messages',
            content_rowid='id',
            tokenize='trigram'
        )
        """
    )
    # Sync triggers — INSERT/UPDATE/DELETE on chat_messages keep the
    # FTS index in lockstep. Pattern lifted from SQLite's FTS5 docs.
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS chat_messages_ai AFTER INSERT ON chat_messages BEGIN
            INSERT INTO chat_messages_fts(rowid, text) VALUES (new.id, new.text);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS chat_messages_ad AFTER DELETE ON chat_messages BEGIN
            INSERT INTO chat_messages_fts(chat_messages_fts, rowid, text) VALUES ('delete', old.id, old.text);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS chat_messages_au AFTER UPDATE ON chat_messages BEGIN
            INSERT INTO chat_messages_fts(chat_messages_fts, rowid, text) VALUES ('delete', old.id, old.text);
            INSERT INTO chat_messages_fts(rowid, text) VALUES (new.id, new.text);
        END
        """
    )

    op.create_table(
        "task_presets",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("key", sa.String(length=64), nullable=False, unique=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("frequency", sa.String(length=16), nullable=False),
        sa.Column("hour", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("minute", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("day_of_week", sa.Integer()),
        sa.Column("day_of_month", sa.Integer()),
        sa.Column("run_at", sa.String(length=32)),
        sa.Column("channel", sa.String(length=16), nullable=False, server_default="webui"),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
    )

    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("cron", sa.String(length=120), nullable=False),
        sa.Column("run_at", sa.String(length=32)),
        sa.Column("tz", sa.String(length=64), nullable=False, server_default="UTC"),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("delivery_to", sa.String(length=128)),
        sa.Column("session_id", sa.String(length=26),
                  sa.ForeignKey("chat_sessions.session_id", ondelete="SET NULL")),
        sa.Column("uid", sa.Integer(),
                  sa.ForeignKey("contacts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("preset_id", sa.String(length=26),
                  sa.ForeignKey("task_presets.id", ondelete="SET NULL")),
        sa.Column("preset_key", sa.String(length=64)),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_run_at", sa.String(length=32)),
        sa.Column("last_status", sa.String(length=16)),
        sa.Column("last_error", sa.String(length=500)),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
    )
    op.create_unique_constraint("uq_tasks_name", "tasks", ["name"])
    op.create_index("ix_tasks_enabled_last_run", "tasks", ["enabled", "last_run_at"])
    op.create_index("ix_tasks_contact", "tasks", ["uid"])
    op.create_index("ix_tasks_preset_key", "tasks", ["preset_key"])

    op.create_table(
        "task_runs",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("task_id", sa.String(length=26),
                  sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", sa.String(length=26),
                  sa.ForeignKey("chat_sessions.session_id", ondelete="SET NULL")),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.String(length=32), nullable=False),
        sa.Column("finished_at", sa.String(length=32)),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.String(length=500)),
        sa.Column("reply_excerpt", sa.String(length=500)),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_task_runs_task_started", "task_runs", ["task_id", "started_at"]
    )

    op.create_table(
        "mcp_servers",
        sa.Column("name", sa.String(length=64), primary_key=True),
        sa.Column("connection_type", sa.String(length=16), nullable=False),
        sa.Column("command", sa.String(length=256)),
        sa.Column("args_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("env_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("url", sa.String(length=512)),
        sa.Column("headers_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("connect_timeout", sa.Float()),
        sa.Column("execute_timeout", sa.Float()),
        sa.Column("sse_read_timeout", sa.Float()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    # ============================================================== #
    # Durable message bus — agent runtime queue + run state.
    # ============================================================== #

    op.create_table(
        "agent_inbox",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(length=64), unique=True, nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=128)),
        sa.Column("correlation_id", sa.String(length=128)),
        sa.Column("causation_id", sa.String(length=128)),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=32)),
        sa.Column("source_id", sa.String(length=128)),
        sa.Column("external_event_id", sa.String(length=128)),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("leased_by", sa.String(length=128)),
        sa.Column("leased_until", sa.DateTime()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_agent_inbox_claim", "agent_inbox", ["status", "available_at", "id"])
    op.create_index("ix_agent_inbox_lease", "agent_inbox", ["status", "leased_until"])
    op.create_index("ix_agent_inbox_run", "agent_inbox", ["run_id", "id"])
    op.create_index("ix_agent_inbox_conversation", "agent_inbox", ["conversation_id", "id"])
    op.create_index(
        "ux_agent_inbox_source_external",
        "agent_inbox",
        ["source_type", "source_id", "external_event_id"],
        unique=True,
        sqlite_where=sa.text("external_event_id IS NOT NULL"),
    )

    op.create_table(
        "agent_runs",
        sa.Column("run_id", sa.String(length=64), primary_key=True),
        sa.Column("root_event_id", sa.String(length=64), unique=True, nullable=False),
        sa.Column("conversation_id", sa.String(length=128)),
        sa.Column("correlation_id", sa.String(length=128)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("continuation", sa.JSON()),
        sa.Column("result", sa.JSON()),
        sa.Column("error_code", sa.String(length=96)),
        sa.Column("error_detail", sa.Text()),
        sa.Column("expected_tool_call_ids", sa.JSON()),
        sa.Column("expected_a2a_invocation_ids", sa.JSON()),
        sa.Column("iteration_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("token_usage", sa.JSON()),
        sa.Column("deadline_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_agent_runs_status_created", "agent_runs", ["status", "created_at"])
    op.create_index(
        "ix_agent_runs_conversation_status",
        "agent_runs",
        ["conversation_id", "status", "created_at"],
    )
    op.create_index("ix_agent_runs_deadline", "agent_runs", ["deadline_at"])

    op.create_table(
        "run_inputs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=64), unique=True, nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("source_event_id", sa.String(length=64)),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("received_seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("context_seq", sa.Integer()),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_run_inputs_run_created", "run_inputs", ["run_id", "created_at", "id"]
    )
    op.create_index(
        "ix_run_inputs_run_status_seq",
        "run_inputs",
        ["run_id", "status", "received_seq"],
    )
    op.create_index(
        "ix_run_inputs_source_event_id", "run_inputs", ["source_event_id"]
    )

    op.create_table(
        "tool_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(length=64), unique=True, nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("tool_call_id", sa.String(length=128), unique=True, nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("tool_source", sa.String(length=128)),
        sa.Column("catalog_revision", sa.Integer()),
        sa.Column("schema_hash", sa.String(length=64)),
        sa.Column("idempotency_key", sa.String(length=160)),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("leased_by", sa.String(length=128)),
        sa.Column("leased_until", sa.DateTime()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_tool_jobs_claim", "tool_jobs", ["status", "available_at", "id"])
    op.create_index(
        "ux_tool_jobs_idempotency",
        "tool_jobs",
        ["idempotency_key"],
        unique=True,
        sqlite_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tool_call_id", sa.String(length=128), unique=True, nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="requested"),
        sa.Column("result", sa.JSON()),
        sa.Column("error", sa.JSON()),
        sa.Column("ordinal", sa.Integer()),
        sa.Column("ordered_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime()),
    )
    op.create_index("ix_tool_calls_run_created", "tool_calls", ["run_id", "created_at"])
    op.create_index("ix_tool_calls_run_ordinal", "tool_calls", ["run_id", "ordinal"])

    op.create_table(
        "delivery_outbox",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("delivery_id", sa.String(length=64), unique=True, nullable=False),
        sa.Column("run_id", sa.String(length=64)),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("destination", sa.String(length=256)),
        sa.Column("event_id", sa.String(length=64)),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160)),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("leased_by", sa.String(length=128)),
        sa.Column("leased_until", sa.DateTime()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_delivery_outbox_claim", "delivery_outbox", ["status", "available_at", "id"]
    )
    op.create_index(
        "ux_delivery_outbox_event_id",
        "delivery_outbox",
        ["event_id"],
        unique=True,
        sqlite_where=sa.text("event_id IS NOT NULL"),
    )
    op.create_index(
        "ux_delivery_outbox_idempotency",
        "delivery_outbox",
        ["idempotency_key"],
        unique=True,
        sqlite_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "a2a_invocations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("invocation_id", sa.String(length=128), unique=True, nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=512), nullable=False),
        sa.Column("tool_call_id", sa.String(length=128), unique=True),
        sa.Column("request_event_id", sa.String(length=128), unique=True),
        sa.Column("reply_to", sa.String(length=128)),
        sa.Column("expect_reply", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("deadline_at", sa.DateTime()),
        sa.Column("idempotency_key", sa.String(length=160), unique=True),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="requested"),
        sa.Column("result", sa.JSON()),
        sa.Column("error", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime()),
    )
    op.create_index(
        "ix_a2a_invocations_run_created",
        "a2a_invocations",
        ["run_id", "created_at"],
    )

    op.create_table(
        "llm_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("attempt_id", sa.String(length=128), unique=True, nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("inbox_event_id", sa.String(length=64)),
        sa.Column("provider", sa.String(length=64)),
        sa.Column("model", sa.String(length=128)),
        sa.Column("last_stream_seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("phase", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="started"),
        sa.Column("request", sa.JSON()),
        sa.Column("response", sa.JSON()),
        sa.Column("error", sa.JSON()),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime()),
    )
    op.create_index(
        "ix_llm_attempts_run_started", "llm_attempts", ["run_id", "started_at"]
    )

    op.create_table(
        "tool_definitions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("input_schema_json", sa.JSON(), nullable=False),
        sa.Column("allowed_roles_json", sa.JSON()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("implementation_version", sa.String(length=128)),
        sa.Column("schema_hash", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_tool_definitions_source_name", "tool_definitions", ["source", "name"]
    )
    op.create_index(
        "ix_tool_definitions_enabled",
        "tool_definitions",
        ["enabled", "source", "name"],
    )

    op.create_table(
        "tool_catalog_state",
        sa.Column("singleton_key", sa.String(length=64), primary_key=True),
        sa.Column("current_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    # ============================================================== #
    # Public MAGIS PostgreSQL schema (also seeded into private
    # SQLite for dev-mode collapsed deployments — `Base` is shared).
    # ============================================================== #

    op.create_table(
        "magic",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100)),
        sa.Column("provider", sa.String(length=64)),
        sa.Column("api_key", sa.String(length=256)),
        sa.Column("instruction", sa.String(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "magis",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False, unique=True),
        sa.Column("parent_id", sa.Integer(),
                  sa.ForeignKey("magis.id", ondelete="RESTRICT")),
        sa.Column("adam_id", sa.Integer(),
                  sa.ForeignKey("magic.id", ondelete="SET NULL")),
        sa.Column("instruction", sa.String(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "magis_roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("magis_id", sa.Integer(),
                  sa.ForeignKey("magis.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_reserved", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_magis_roles_magis_name", "magis_roles", ["magis_id", "name"]
    )

    op.create_table(
        "magis_memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("magis_id", sa.Integer(),
                  sa.ForeignKey("magis.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("magic_id", sa.Integer(),
                  sa.ForeignKey("magic.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("role_id", sa.Integer(),
                  sa.ForeignKey("magis_roles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_magis_memberships_magic", "magis_memberships", ["magic_id"]
    )

    op.create_table(
        "magis_admins",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("magis_id", sa.Integer(),
                  sa.ForeignKey("magis.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False, index=True),
        sa.Column("display_name", sa.String(length=120)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_magis_admins_magis_telegram", "magis_admins", ["magis_id", "telegram_id"]
    )

    op.create_table(
        "eve_runtimes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("magic_id", sa.Integer(),
                  sa.ForeignKey("magic.id", ondelete="CASCADE"),
                  unique=True, nullable=False, index=True),
        sa.Column("desired_state", sa.String(length=16), nullable=False,
                  server_default="draft"),
        sa.Column("observed_state", sa.String(length=16), nullable=False,
                  server_default="draft"),
        sa.Column("namespace", sa.String(length=63)),
        sa.Column("deployment_name", sa.String(length=63)),
        sa.Column("workspace_claim_name", sa.String(length=63)),
        sa.Column("credential_secret_name", sa.String(length=63)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "control_settings",
        sa.Column("key", sa.String(length=191), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "control_operators",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False, unique=True,
                  index=True),
        sa.Column("display_name", sa.String(length=120)),
        sa.Column("admin", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    # ============================================================== #
    # Legacy raw-SQL bootstrap table (kept for backwards compat with
    # the ``meta`` KV used by older runtime code).
    # ============================================================== #

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    op.execute(
        "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', '0')"
    )


def downgrade() -> None:
    """Reverse the single-shot schema.

    Drop order roughly mirrors creation order, with FK references
    in mind. The legacy ``meta`` table is dropped last because
    nothing references it.
    """
    op.execute("DROP TRIGGER IF EXISTS chat_messages_au")
    op.execute("DROP TRIGGER IF EXISTS chat_messages_ad")
    op.execute("DROP TRIGGER IF EXISTS chat_messages_ai")
    op.execute("DROP TABLE IF EXISTS chat_messages_fts")
    op.execute("DROP TABLE IF EXISTS control_operators")
    op.execute("DROP TABLE IF EXISTS control_settings")
    op.execute("DROP TABLE IF EXISTS eve_runtimes")
    op.execute("DROP TABLE IF EXISTS magis_admins")
    op.execute("DROP TABLE IF EXISTS magis_memberships")
    op.execute("DROP TABLE IF EXISTS magis_roles")
    op.execute("DROP TABLE IF EXISTS magis")
    op.execute("DROP TABLE IF EXISTS magic")
    op.execute("DROP TABLE IF EXISTS tool_catalog_state")
    op.execute("DROP TABLE IF EXISTS tool_definitions")
    op.execute("DROP TABLE IF EXISTS llm_attempts")
    op.execute("DROP TABLE IF EXISTS a2a_invocations")
    op.execute("DROP TABLE IF EXISTS delivery_outbox")
    op.execute("DROP TABLE IF EXISTS tool_calls")
    op.execute("DROP TABLE IF EXISTS tool_jobs")
    op.execute("DROP TABLE IF EXISTS run_inputs")
    op.execute("DROP TABLE IF EXISTS agent_runs")
    op.execute("DROP TABLE IF EXISTS agent_inbox")
    op.execute("DROP TABLE IF EXISTS mcp_servers")
    op.execute("DROP TABLE IF EXISTS task_runs")
    op.execute("DROP TABLE IF EXISTS tasks")
    op.execute("DROP TABLE IF EXISTS task_presets")
    op.execute("DROP TABLE IF EXISTS chat_messages")
    op.execute("DROP TABLE IF EXISTS chat_sessions")
    op.execute("DROP TABLE IF EXISTS auth_credentials")
    op.execute("DROP TABLE IF EXISTS action_items")
    op.execute("DROP TABLE IF EXISTS token_usage")
    op.execute("DROP TABLE IF EXISTS memory_entries")
    op.execute("DROP TABLE IF EXISTS contact_notes")
    op.execute("DROP TABLE IF EXISTS contacts")
    op.execute("DROP TABLE IF EXISTS settings")
    op.execute("DROP TABLE IF EXISTS meta")