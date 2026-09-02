"""Drop AppendMessageJob; ChatNotify and DeliveryNotify write messages.

Revision ID: 0.0.55
Revises: 0.0.54
Create Date: 2026-09-01
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "0.0.55"
down_revision = "0.0.54"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "jobs_append_message" in inspector.get_table_names():
        op.drop_table("jobs_append_message")


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
