"""Add local Contact and ContactNote Books.

Revision ID: 0.0.8
Revises: 0.0.7
Create Date: 2026-08-27
"""

from __future__ import annotations

from alembic import op

revision = "0.0.8"
down_revision = "0.0.7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from magi.bus.firmware.versions.schema import firmware_metadata

    firmware_metadata().create_all(bind=op.get_bind())


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
