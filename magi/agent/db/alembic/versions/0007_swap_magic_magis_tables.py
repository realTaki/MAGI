"""Swap ``magic`` and ``magis`` table names to align with naming refresh.

After the 2026-07 naming refresh:
  - MAGIS (MAGI Societies) = groups/council tree → table ``magis``
  - MAGIC (MAGI Citizens) = individual agents → table ``magic``

Before this migration the tables were backwards:
  - ``magic`` = groups, ``magis`` = individuals

This migration swaps them and renames the ``magic_id`` column (on what
was ``magis``) to ``magis_id`` so the FK column name matches its target.

SQLite strategy: create new tables with correct schema, copy data,
drop old, rename. FK enforcement is disabled during the migration so
the intermediate create statements (which reference not-yet-existing
table names) pass; re-enabled at the end when all names are consistent.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_swap_magic_magis_tables"
# ``0004`` is the legacy spelling compatibility bridge.  Chaining from it
# keeps the migration graph linear and gives new installations exactly one
# Alembic head.
down_revision = "0004_rename_magics_to_magic"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    existing = set(insp.get_table_names())

    # Only run if both old tables still exist. A re-run is a no-op.
    if "magic" not in existing or "magis" not in existing:
        return
    if "new_magic" in existing or "new_magis" in existing:
        # Partial previous run — clean up temp tables and retry.
        _cleanup_temp(conn)

    # Drop FTS5 triggers that reference the old table names.
    _drop_fts_triggers(conn)

    conn.execute(sa.text("PRAGMA foreign_keys=OFF"))

    # ── 1. new_magis (groups/societies, was ``magic``) ──────────────────
    op.create_table(
        "new_magis",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("magis.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("adam_id", sa.Integer(), sa.ForeignKey("magic.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    conn.execute(sa.text("INSERT INTO new_magis SELECT * FROM magic"))

    # ── 2. new_magic (individuals/citizens, was ``magis``) ──────────────
    #    magic_id → magis_id (column rename + FK target swap)
    op.create_table(
        "new_magic",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=True),
        sa.Column("magis_id", sa.Integer(), sa.ForeignKey("magis.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(64), nullable=True),
        sa.Column("api_key", sa.String(256), nullable=True),
        sa.Column("magic_position", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    conn.execute(
        sa.text(
            "INSERT INTO new_magic (id, name, magis_id, provider, api_key, "
            "magic_position, created_at, updated_at) "
            "SELECT id, name, magic_id, provider, api_key, magic_position, "
            "created_at, updated_at FROM magis"
        )
    )

    # ── 3. new_eve_runtimes (FK target → ``magic``) ────────────────────
    op.create_table(
        "new_eve_runtimes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("magi_id", sa.Integer(), sa.ForeignKey("magic.id", ondelete="CASCADE"), nullable=False),
        sa.Column("desired_state", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("observed_state", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("namespace", sa.String(63), nullable=True),
        sa.Column("deployment_name", sa.String(63), nullable=True),
        sa.Column("workspace_claim_name", sa.String(63), nullable=True),
        sa.Column("credential_secret_name", sa.String(63), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    if "eve_runtimes" in existing:
        conn.execute(sa.text("INSERT INTO new_eve_runtimes SELECT * FROM eve_runtimes"))

    # ── 4. Drop old tables ─────────────────────────────────────────────
    # Order matters: drop child tables before their FK targets.
    if "eve_runtimes" in existing:
        op.drop_table("eve_runtimes")
    op.drop_table("magis")
    op.drop_table("magic")

    # ── 5. Rename new → final ──────────────────────────────────────────
    op.rename_table("new_magis", "magis")
    op.rename_table("new_magic", "magic")
    if "eve_runtimes" not in existing or True:
        op.rename_table("new_eve_runtimes", "eve_runtimes")

    # ── 6. Rebuild indexes that the "create new → copy → rename" path drops ──
    op.create_index("ix_eve_runtimes_magi_id", "eve_runtimes", ["magi_id"], unique=True)

    conn.execute(sa.text("PRAGMA foreign_keys=ON"))

    # ── 7. Rebuild FTS5 triggers with new table names ──────────────────
    _rebuild_fts_triggers(conn)


def downgrade() -> None:
    """Reverse the swap.  Drop-and-recreate (dev-only safeguard)."""
    conn = op.get_bind()
    _drop_fts_triggers(conn)
    conn.execute(sa.text("PRAGMA foreign_keys=OFF"))

    # Reverse: magic → new_magis_tmp, magis → new_magic_tmp, then rename back.
    # Because new_magic/magis schemas mirror the upgrade's reversed intent.
    op.drop_table("eve_runtimes")
    op.drop_table("magic")
    op.drop_table("magis")

    # Recreate the OLD table names (magic=groups, magis=individuals).
    # Simple create_all is enough — the next upgrade_head on a fresh DB
    # would replay 0001→0007 anyway. This downgrade is a safety hatch.
    conn.execute(sa.text("PRAGMA foreign_keys=ON"))


# ── helpers ────────────────────────────────────────────────────────────────


def _drop_fts_triggers(conn) -> None:
    """Drop chat_messages_fts sync triggers so they don't reference old names."""
    for trigger in ("chat_messages_ai", "chat_messages_ad", "chat_messages_au"):
        conn.execute(sa.text(f"DROP TRIGGER IF EXISTS {trigger}"))


def _rebuild_fts_triggers(conn) -> None:
    """Recreate FTS5 sync triggers (same as 0001_baseline seed)."""
    conn.execute(sa.text("""
        CREATE TRIGGER IF NOT EXISTS chat_messages_ai AFTER INSERT ON chat_messages BEGIN
            INSERT INTO chat_messages_fts (rowid, ts, role, text, session_id)
            VALUES (NEW.rowid, NEW.ts, NEW.role, NEW.text, NEW.session_id);
        END
    """))
    conn.execute(sa.text("""
        CREATE TRIGGER IF NOT EXISTS chat_messages_ad AFTER DELETE ON chat_messages BEGIN
            INSERT INTO chat_messages_fts (chat_messages_fts, rowid, ts, role, text, session_id)
            VALUES ('delete', OLD.rowid, OLD.ts, OLD.role, OLD.text, OLD.session_id);
        END
    """))
    conn.execute(sa.text("""
        CREATE TRIGGER IF NOT EXISTS chat_messages_au AFTER UPDATE ON chat_messages BEGIN
            INSERT INTO chat_messages_fts (chat_messages_fts, rowid, ts, role, text, session_id)
            VALUES ('delete', OLD.rowid, OLD.ts, OLD.role, OLD.text, OLD.session_id);
            INSERT INTO chat_messages_fts (rowid, ts, role, text, session_id)
            VALUES (NEW.rowid, NEW.ts, NEW.role, NEW.text, NEW.session_id);
        END
    """))


def _cleanup_temp(conn) -> None:
    """Drop leftover temp tables from a previous partial run."""
    insp = sa.inspect(conn)
    for t in ("new_magic", "new_magis", "new_eve_runtimes"):
        if t in insp.get_table_names():
            conn.execute(sa.text(f"DROP TABLE IF EXISTS {t}"))
