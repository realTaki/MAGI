"""Split Conversation ownership from additional group participants.

Revision ID: 0.0.14
Revises: 0.0.13
Create Date: 2026-08-28
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import Integer, inspect

revision = "0.0.14"
down_revision = "0.0.13"
branch_labels = None
depends_on = None


def _rename_contact_owner_column(table_name: str) -> None:
    inspector = inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if "contact_id" not in columns or "owner_contact_id" in columns:
        return
    with op.batch_alter_table(table_name) as batch:
        batch.alter_column(
            "contact_id",
            new_column_name="owner_contact_id",
            existing_type=Integer(),
        )


def upgrade() -> None:
    # Fresh databases receive both the new Book and its command tables here;
    # existing databases retain their conversation rows and only rename the
    # ownership field.
    from magi.new_bus.firmware.versions.schema import firmware_metadata

    firmware_metadata().create_all(bind=op.get_bind())
    _rename_contact_owner_column("books_conversations")
    _rename_contact_owner_column("jobs_create_conversation")


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
