"""Create the optional FTS5 chat search structures."""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

from magi.agent.db.migrations import _FTS_MIGRATIONS

revision = "0002_fts5"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
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

    if not has_fts5:
        return

    try:
        for _name, ddl in _FTS_MIGRATIONS:
            bind.execute(text(ddl))
        bind.execute(
            text(
                "INSERT INTO chat_messages_fts(chat_messages_fts) "
                "VALUES('rebuild')"
            )
        )
    except Exception:
        # FTS is an optional acceleration layer. A stripped SQLite build
        # should still boot; the search route reports unavailable instead.
        # The migration remains recorded so every restart does not fail on
        # the same optional feature.
        return


def downgrade() -> None:
    bind = op.get_bind()
    for name in (
        "chat_messages_au",
        "chat_messages_ad",
        "chat_messages_ai",
    ):
        bind.execute(text(f"DROP TRIGGER IF EXISTS {name}"))
    bind.execute(text("DROP TABLE IF EXISTS chat_messages_fts"))
