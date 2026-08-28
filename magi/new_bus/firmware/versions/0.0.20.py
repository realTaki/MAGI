"""Allow a missing skill to be represented by a null result payload.

Revision ID: 0.0.20
Revises: 0.0.19
Create Date: 2026-08-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0.0.20"
down_revision = "0.0.19"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("jobs_get_skill") as batch_op:
        batch_op.alter_column("content", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
