"""Add the provider configuration-change Job.

Revision ID: 0.0.7
Revises: 0.0.6
Create Date: 2026-08-27
"""

from __future__ import annotations

from alembic import op

revision = "0.0.7"
down_revision = "0.0.6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from bus.firmware.versions.schema import firmware_metadata

    firmware_metadata().create_all(bind=op.get_bind())


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
