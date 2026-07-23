"""Pre-Alembic inline migrations + FTS5 sync triggers.

SQLAlchemy's ``create_all`` is a no-op when the table already
exists, so it can't add a new column to an existing table. For
C1.1 we have a small list of known migrations to run by hand;
the first Alembic baseline (end of C1.3) takes over from here.

Each entry in :data:`_INLINE_MIGRATIONS` is
``(table, column, ddl_fragment)``. ``ddl_fragment`` is the part
after the column name, e.g. ``"INTEGER REFERENCES
departments(id)"``. NULL is the default, so existing rows
survive the add.

Columns that need a UNIQUE constraint are listed separately
in :data:`_UNIQUE_INDEX_MIGRATIONS` because SQLite refuses
``ALTER TABLE ... ADD COLUMN ... UNIQUE`` ("Cannot add a
UNIQUE column") on pre-existing tables. The workaround is
to add the column plain, then create the unique index.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine


logger = logging.getLogger("magi.agent.db.migrations")


_INLINE_MIGRATIONS: list[tuple[str, str, str]] = [
    # C1.1: added department_id, provider, api_key to employees.
    ("employees", "department_id", "INTEGER REFERENCES departments(id) ON DELETE SET NULL"),
    ("employees", "provider", "VARCHAR(32)"),
    ("employees", "api_key", "VARCHAR(512)"),
    # C1.1 (soft-delete): separated_at lets the dashboard mark
    # an employee as 离职 without losing the row.
    ("employees", "separated_at", "DATETIME"),
    # C1.x (role + TG binding): unifies the WebUI Access list
    # with the employees table. Existing rows default to
    # role='assigned' (in v0 single-instance, this MAGI
    # serves every employee); telegram_id stays NULL until
    # the /start binding flow runs. The UNIQUE constraint
    # on telegram_id is added as a separate index step
    # below (SQLite can't ALTER TABLE ADD COLUMN with
    # UNIQUE).
    ("employees", "role", "VARCHAR(16) NOT NULL DEFAULT 'assigned'"),
    ("employees", "telegram_id", "BIGINT"),
    # Tasks: ``run_at`` carries an ISO datetime string for
    # one-shot ("once") tasks. Nullable — cron-driven rows
    # keep it NULL. The ISO round-trip check runs at the
    # API boundary, not as a SQLite CHECK (can't ADD
    # COLUMN ... CHECK).
    ("tasks", "run_at", "VARCHAR(32)"),
    # Tasks: ``delivery_to`` carries the destination per
    # ``channel`` — TG tgid (digits), or an email
    # address once that runner lands. Webui tasks leave
    # it NULL (the task's session IS the operator-visible
    # record; no separate IM target). Nullable: legacy
    # rows fall back to the operator's bound destination
    # at fire time until the operator edits them.
    ("tasks", "delivery_to", "VARCHAR(128)"),
    # Tasks: ``session_id`` points at the agent's home
    # session (channel="task"). Allocated at task creation
    # time so cron fires accumulate into one
    # conversation per task; the runner never creates
    # sessions, only loads + appends. Nullable for legacy
    # rows; the runner's session-resolution branch falls
    # back to "allocate on first fire" until the operator
    # edits them. SET NULL on delete: deleting a task
    # leaves the session in place as a record.
    ("tasks", "session_id", "VARCHAR(26)"),
]

# Column renames. ``(table, old_name, new_name)``. The
# migration is a one-shot ``ALTER TABLE ... RENAME COLUMN``
# (SQLite 3.25+; CPython 3.12 ships 3.45+) executed the
# first time a database is opened with the new column name
# present and the old one absent. Re-runs on the same DB
# are no-ops. D.18+1 renamed ``chat_sessions.tgid`` →
# ``chat_sessions.tgid`` so the column's purpose
# (Telegram chat identifier only, NOT a generic chat id)
# is reflected in its name; the WebUI/TG future-IM
# cross-platform scope now lives on ``uid``.
_RENAME_COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    (
        "chat_sessions",
        "tgid",
        "tgid",
    ),
    (
        "chat_sessions",
        "uid",
        "uid",
    ),
    (
        "tasks",
        "uid",
        "uid",
    ),
    (
        "action_items",
        "uid",
        "uid",
    ),
    (
        "token_usage",
        "uid",
        "uid",
    ),
    (
        "memories",
        "uid",
        "uid",
    ),
]

# Plain index pairs. ``(table, index_name, columns_ddl)``.
# Run after the plain ALTER TABLE above for read-side speed.
# Idempotent (``CREATE INDEX IF NOT EXISTS``).
_INDEX_MIGRATIONS: list[tuple[str, str, str]] = [
    # Speeds up ``GET /api/action_items`` which always filters
    # by uid; the second index supports the
    # "open + last-7-days completed" listing ordered by recency.
    (
        "action_items",
        "ix_action_items_employee_id",
        "(uid)",
    ),
    (
        "action_items",
        "ix_action_items_employee_recent",
        "(uid, created_at DESC)",
    ),
    # D.15 — token-bill aggregation. ``create_all`` builds
    # this alongside the new ``token_usage`` table on fresh
    # installs; the ``CREATE INDEX IF NOT EXISTS`` here
    # covers existing DBs (the inline migration runner is
    # idempotent). The composite covers the
    # ``WHERE uid = ? AND ts BETWEEN ? AND ?`` query
    # the per-period endpoint issues.
    (
        "token_usage",
        "ix_token_usage_emp_ts",
        "(uid, ts)",
    ),
    # 定时 / 循环任务 (proactive runtime) — indexes
    # backfilled for existing DBs that pre-date the
    # proactive feature. The model declares the same
    # names in __table_args__; on fresh installs
    # ``create_all`` builds these alongside the new
    # tables. ``tasks(enabled, last_run_at)`` covers the
    # scheduler boot scan ("what's enabled and possibly
    # due?") and the operator's primary listing. The
    # ``task_runs`` composite covers the history pane's
    # primary access pattern: per task, ordered by
    # started_at desc.
    (
        "tasks",
        "ix_tasks_enabled_last_run",
        "(enabled, last_run_at)",
    ),
    (
        "tasks",
        "ix_tasks_employee",
        "(uid)",
    ),
    (
        "task_runs",
        "ix_task_runs_task_started",
        "(task_id, started_at)",
    ),
]

# Unique-index triples. ``(table, index_name, columns_ddl,
# where_clause_or_None)``. The where_clause is a partial-index
# predicate; ``None`` falls back to ``WHERE <last_column> IS
# NOT NULL`` (the original behaviour for the employees
# telegram_id index, which is nullable for non-bound rows).
_UNIQUE_INDEX_MIGRATIONS: list[tuple[str, str, str, str | None]] = [
    (
        "employees",
        "ux_employees_telegram_id",
        "telegram_id",
        None,
    ),
    # Action items: idempotency — one OPEN row per
    # ``(uid, kind)``. ``Partial unique`` so a
    # completed/dismissed row doesn't block a future same-kind
    # prompt (e.g. operator removes admin, re-adds them:
    # a future prompt of the same kind must be allow-listed).
    (
        "action_items",
        "ux_action_items_open_per_kind",
        "uid, kind",
        "completed_at IS NULL AND dismissed = 0",
    ),
]


# -- FTS5 virtual table (D.18 search) ----------------------------------------
#
# ``chat_messages_fts`` is an external-content FTS5 table that
# mirrors ``chat_messages.text``. Three triggers (ai / ad / au) keep
# it in sync with INSERT / DELETE / UPDATE on the source table.
#
# Tokenizer choice — ``trigram``:
#
#   - CJK: 3-character substring match. E.g. searching "压缩触发"
#     finds messages containing that 3-character run anywhere in
#     the text. Without trigram (with default unicode61), CJK runs
#     are a single token and only exact-prefix matches return.
#   - Latin / digits: same 3-char substring semantics; matches
#     "son" inside "Jefferson" etc. ``LIKE``-style behaviour
#     without the operator-vocabulary quirks of LIKE patterns.
#
# pysqlite3-binary wheels deliberately don't ship ICU, so the
# ``tokenize='icu'`` route that would give true CJK word
# segmentation requires a self-compiled SQLite + libicu link.
# Trigram is the "good enough for v0, no extra build cost" pick.
# Operators who type a single CJK character get a "use at least
# 3 characters" hint from the search UI; everything >=3 chars
# just works.
#
# If FTS5 itself is missing from the linked SQLite (rare on
# CPython 3.12 builds, but possible on stripped-down distros),
# the CREATE TABLE DDL fails. We catch the failure, log a warning,
# and let ``chat_search`` route return 503 ``search.unavailable``.
# The ORM init does NOT abort, so a botched FTS install can't
# brick the whole node.

_FTS_MIGRATIONS: list[tuple[str, str]] = [
    # Virtual table. ``content='chat_messages'`` means the FTS5
    # index doesn't store a copy of the text — it pulls live
    # from the source row by rowid at query time. The downside
    # (slower snippet() reads) is irrelevant at v0 scale;
    # the upside (no double-storage, no drift) is huge.
    (
        "chat_messages_fts",
        "CREATE VIRTUAL TABLE IF NOT EXISTS chat_messages_fts USING fts5("
        "    text, "
        "    content='chat_messages', "
        "    content_rowid='id', "
        "    tokenize='trigram'"
        ")",
    ),
    # Sync triggers. The standard 3-trigger external-content
    # pattern from SQLite's FTS5 docs.
    (
        "chat_messages_ai",
        "CREATE TRIGGER IF NOT EXISTS chat_messages_ai AFTER INSERT ON chat_messages BEGIN "
        "    INSERT INTO chat_messages_fts(rowid, text) VALUES (new.id, new.text); "
        "END",
    ),
    (
        "chat_messages_ad",
        "CREATE TRIGGER IF NOT EXISTS chat_messages_ad AFTER DELETE ON chat_messages BEGIN "
        "    INSERT INTO chat_messages_fts(chat_messages_fts, rowid, text) "
        "        VALUES('delete', old.id, old.text); "
        "END",
    ),
    (
        "chat_messages_au",
        "CREATE TRIGGER IF NOT EXISTS chat_messages_au AFTER UPDATE ON chat_messages BEGIN "
        "    INSERT INTO chat_messages_fts(chat_messages_fts, rowid, text) "
        "        VALUES('delete', old.id, old.text); "
        "    INSERT INTO chat_messages_fts(rowid, text) VALUES (new.id, new.text); "
        "END",
    ),
]


def _run_inline_migrations(engine: Engine) -> None:
    with engine.begin() as conn:
        # Column renames first — once the column is renamed
        # to its new name, the ``CREATE TABLE`` of a fresh DB
        # that already declares the new column will see
        # ``table_info`` reflect it, and the migrations
        # below that key off ``table_info`` won't try to
        # re-create it.
        for table, old_name, new_name in _RENAME_COLUMN_MIGRATIONS:
            existing = {
                row[1]
                for row in conn.execute(text(f"PRAGMA table_info({table})"))
            }
            if new_name in existing:
                # Already migrated (or fresh DB).
                continue
            if old_name not in existing:
                # Fresh DB with the new schema — nothing to
                # rename (CREATE TABLE declared ``new_name``
                # directly).
                continue
            logger.info(
                "inline migration: renaming %s.%s → %s",
                table, old_name, new_name,
            )
            conn.execute(
                text(
                    f"ALTER TABLE {table} "
                    f"RENAME COLUMN {old_name} TO {new_name}"
                )
            )

        for table, column, ddl in _INLINE_MIGRATIONS:
            # PRAGMA table_info returns one row per column; the
            # second element is the column name.
            existing = {
                row[1]
                for row in conn.execute(text(f"PRAGMA table_info({table})"))
            }
            if column in existing:
                continue
            logger.info(
                "inline migration: adding %s.%s",
                table,
                column,
            )
            conn.execute(
                text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            )

        for table, index_name, columns in _INDEX_MIGRATIONS:
            logger.info(
                "inline migration: ensuring index %s on %s.%s",
                index_name, table, columns,
            )
            conn.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS "
                    f"{index_name} ON {table} {columns}"
                )
            )

        for table, index_name, columns, where_clause in _UNIQUE_INDEX_MIGRATIONS:
            logger.info(
                "inline migration: ensuring unique index %s on %s.%s",
                index_name, table, columns,
            )
            # ``where_clause`` is None → default to "WHERE
            # <last column> IS NOT NULL" (preserves the original
            # behaviour for ux_employees_telegram_id). For
            # partial indexes (``ux_action_items_open_per_kind``)
            # the caller supplies the actual predicate.
            if where_clause is None:
                last_col = columns.split(",")[-1].strip()
                predicate = f"WHERE {last_col} IS NOT NULL"
            else:
                predicate = f"WHERE {where_clause}"
            conn.execute(
                text(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS "
                    f"{index_name} ON {table} ({columns}) "
                    f"{predicate}"
                )
            )

        # FTS5 virtual table + sync triggers. Probe the compile
        # options first; on a stripped SQLite (e.g. Alpine
        # musllinux without FTS5) we log and skip so ORM init
        # still succeeds — ``chat_search`` returns 503 in that
        # case instead of the whole node refusing to boot.
        try:
            has_fts5 = (
                conn.execute(
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
                for name, ddl in _FTS_MIGRATIONS:
                    logger.info("fts migration: %s", name)
                    conn.execute(text(ddl))
                # External-content FTS indexes start empty —
                # populate from any existing chat_messages rows
                # so a botched restart / partial migration is
                # self-healing.
                conn.execute(
                    text(
                        "INSERT INTO chat_messages_fts(chat_messages_fts) "
                        "VALUES('rebuild')"
                    )
                )
            except Exception as e:
                # Some SQLite builds compile FTS5 but reject
                # ``tokenize='trigram'`` (rare). Treat that the
                # same as "no FTS5" and keep the ORM init alive.
                logger.warning(
                    "fts migration failed (%s); search route will return 503",
                    e,
                )
        else:
            logger.warning(
                "FTS5 not compiled into this SQLite; "
                "chat search will return 503"
            )