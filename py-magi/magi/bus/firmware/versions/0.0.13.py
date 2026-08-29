"""Remove redundant ContactNote date columns.

Revision ID: 0.0.13
Revises: 0.0.12
Create Date: 2026-08-27
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "0.0.13"
down_revision = "0.0.12"
branch_labels = None
depends_on = None

_TABLES = (
    "books_contact_notes",
    "jobs_create_contact_note",
    "jobs_update_contact_note",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    for table_name in _TABLES:
        if table_name not in inspector.get_table_names():
            continue
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if "note_date" not in columns:
            continue
        with op.batch_alter_table(table_name) as batch:
            batch.drop_column("note_date")
    if "jobs_append_daily_contact_note" in inspector.get_table_names():
        op.drop_table("jobs_append_daily_contact_note")


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
