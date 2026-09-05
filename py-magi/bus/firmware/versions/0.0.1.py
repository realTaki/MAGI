"""Current Firmware Book and Job tables.

Revision ID: 0.0.1
Revises:
Create Date: 2026-09-05
"""

from __future__ import annotations

from alembic import op

revision = "0.0.1"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from bus.firmware.versions.schema import firmware_metadata

    firmware_metadata().create_all(bind=op.get_bind())


def downgrade() -> None:
    from bus.firmware.versions.schema import firmware_metadata

    firmware_metadata().drop_all(bind=op.get_bind())
