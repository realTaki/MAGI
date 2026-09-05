"""Add message search Jobs.

Revision ID: 0.0.2
Revises: 0.0.1
Create Date: 2026-09-05
"""

from __future__ import annotations

from alembic import op

revision = "0.0.2"
down_revision = "0.0.1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from bus.firmware.versions.schema import firmware_metadata

    firmware_metadata().create_all(bind=op.get_bind())


def downgrade() -> None:
    op.drop_table("jobs_search_conversation_messages")
    op.drop_table("jobs_search_contact_messages")
