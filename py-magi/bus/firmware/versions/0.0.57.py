"""Add the durable GetConversation command.

Revision ID: 0.0.57
Revises: 0.0.56
Create Date: 2026-09-01
"""

from __future__ import annotations

from alembic import op

revision = "0.0.57"
down_revision = "0.0.56"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from bus.firmware.versions.schema import firmware_metadata

    firmware_metadata().create_all(bind=op.get_bind())


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
