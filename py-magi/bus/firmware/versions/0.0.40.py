"""Rename ChangeProvider Job table to the Notify name.

Revision ID: 0.0.40
Revises: 0.0.39
Create Date: 2026-08-30
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "0.0.40"
down_revision = "0.0.39"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "jobs_change_provider" in tables and "jobs_change_provider_notify" not in tables:
        op.rename_table("jobs_change_provider", "jobs_change_provider_notify")


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
