"""Add optional, Job-specific Book-result snapshot columns.

Revision ID: 0.0.11
Revises: 0.0.10
Create Date: 2026-08-27
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import JSON, Column, inspect

revision = "0.0.11"
down_revision = "0.0.10"
branch_labels = None
depends_on = None

_COLUMNS = {
    "jobs_list_conversation_messages": ("messages", JSON),
    "jobs_get_contact": ("contact", JSON),
    "jobs_list_contacts": ("contacts", JSON),
    "jobs_get_contact_note": ("contact_note", JSON),
    "jobs_list_contact_notes": ("contact_notes", JSON),
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    for table_name, (column_name, column_type) in _COLUMNS.items():
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if column_name not in columns:
            op.add_column(table_name, Column(column_name, column_type, nullable=True))


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
