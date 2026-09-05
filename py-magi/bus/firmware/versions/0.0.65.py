"""Add GetConversationForChannelJob.

Revision ID: 0.0.65
Revises: 0.0.64
Create Date: 2026-09-04
"""

from __future__ import annotations

from alembic import op

revision = "0.0.65"
down_revision = "0.0.64"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from bus.firmware.versions.schema import firmware_metadata

    firmware_metadata().create_all(bind=op.get_bind())


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
