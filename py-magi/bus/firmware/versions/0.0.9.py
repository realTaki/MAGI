"""Wire Conversation and TokenUsage contact_id foreign keys.

Revision ID: 0.0.9
Revises: 0.0.8
Create Date: 2026-08-27
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "0.0.9"
down_revision = "0.0.8"
branch_labels = None
depends_on = None


_KEYS = (
    (
        "books_conversations",
        "fk_books_conversations_contact_id",
        "books_contacts",
        ["contact_id"],
        ["id"],
        "RESTRICT",
    ),
    (
        "books_token_usage",
        "fk_books_token_usage_contact_id",
        "books_contacts",
        ["contact_id"],
        ["id"],
        "SET NULL",
    ),
)


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for table, name, referred, columns, referred_columns, ondelete in _KEYS:
        if table not in tables or referred not in tables:
            continue
        existing = inspector.get_foreign_keys(table)
        if any(
            item.get("referred_table") == referred and item.get("constrained_columns") == columns
            for item in existing
        ):
            continue
        with op.batch_alter_table(table) as batch:
            batch.create_foreign_key(
                name,
                referred,
                columns,
                referred_columns,
                ondelete=ondelete,
            )


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
