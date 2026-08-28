"""Add BUS Jobs for workspace prompts and skills.

Revision ID: 0.0.16
Revises: 0.0.15
Create Date: 2026-08-28
"""

from __future__ import annotations

from alembic import op

revision = "0.0.16"
down_revision = "0.0.15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from magi.new_bus.firmware.versions.schema import firmware_metadata

    firmware_metadata().create_all(bind=op.get_bind())


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
