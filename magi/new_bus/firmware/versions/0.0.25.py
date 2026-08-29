"""Rename Contact display_name to nickname.

Revision ID: 0.0.25
Revises: 0.0.24
Create Date: 2026-08-29
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import Text, inspect

revision = "0.0.25"
down_revision = "0.0.24"
branch_labels = None
depends_on = None


def _rename_display_name(table_name: str) -> None:
    inspector = inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if "display_name" not in columns or "nickname" in columns:
        return
    with op.batch_alter_table(table_name) as batch:
        batch.alter_column(
            "display_name",
            new_column_name="nickname",
            existing_type=Text(),
        )


def upgrade() -> None:
    _rename_display_name("books_contacts")
    _rename_display_name("jobs_create_contact")
    _rename_display_name("jobs_update_contact")


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
