"""Expand TokenUsage with explicit cache and output token categories.

Revision ID: 0.0.6
Revises: 0.0.5
Create Date: 2026-08-27
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import Column, Integer, inspect

revision = "0.0.6"
down_revision = "0.0.5"
branch_labels = None
depends_on = None

_TOKEN_COLUMNS = (
    "cache_hit_tokens",
    "cache_miss_tokens",
    "cache_write_tokens",
    "thinking_tokens",
    "response_tokens",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    for table_name in ("books_token_usage", "jobs_record_token_usage"):
        if table_name not in tables:
            continue
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        for name in _TOKEN_COLUMNS:
            if name not in existing:
                op.add_column(table_name, Column(name, Integer, nullable=False, server_default="0"))


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
