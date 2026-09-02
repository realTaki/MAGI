"""Rename ContactRole guest to stranger.

Revision ID: 0.0.26
Revises: 0.0.25
Create Date: 2026-08-29
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect, text

revision = "0.0.26"
down_revision = "0.0.25"
branch_labels = None
depends_on = None


def _rewrite_guest_role(table_name: str) -> None:
    inspector = inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if "role" not in columns:
        return
    op.execute(
        text(f"UPDATE {table_name} SET role = 'stranger' WHERE role = 'guest'")
    )


def upgrade() -> None:
    _rewrite_guest_role("books_contacts")
    _rewrite_guest_role("jobs_create_contact")
    _rewrite_guest_role("jobs_update_contact")
    _rewrite_guest_role("jobs_list_contacts")


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
