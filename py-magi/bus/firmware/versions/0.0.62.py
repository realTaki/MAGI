"""Remove internal runtime configuration from Settings.

Revision ID: 0.0.62
Revises: 0.0.61
Create Date: 2026-09-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0.0.62"
down_revision = "0.0.61"
branch_labels = None
depends_on = None

_REMOVED_SETTINGS = (
    "agent.max_tokens",
    "agent.tool_wait_seconds",
    "agent.llm_timeout_seconds",
    "agent.compact_keep_recent",
    "agent.compact_summary_tokens",
    "agent.compact_context_window",
    "asp.handle",
    "asp.base",
    "asp.token",
)


def upgrade() -> None:
    bind = op.get_bind()
    table_name = "books_settings"
    if table_name not in inspect(bind).get_table_names():
        return
    settings = sa.table(table_name, sa.column("key", sa.Text()))
    bind.execute(settings.delete().where(settings.c.key.in_(_REMOVED_SETTINGS)))


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
