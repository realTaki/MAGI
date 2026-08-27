"""Move channel identity out of Contact.

Revision ID: 0.0.12
Revises: 0.0.11
Create Date: 2026-08-27
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "0.0.12"
down_revision = "0.0.11"
branch_labels = None
depends_on = None


def _drop_column_if_present(table_name: str, column_name: str) -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if table_name not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name not in columns:
        return
    with op.batch_alter_table(table_name) as batch:
        batch.drop_column(column_name)


def upgrade() -> None:
    """Keep identity and transport metadata on their separate records."""
    _drop_column_if_present("books_contacts", "tgid")
    _drop_column_if_present("jobs_create_contact", "tgid")
    _drop_column_if_present("jobs_update_contact", "tgid")

    inspector = inspect(op.get_bind())
    if "jobs_get_contact_by_telegram" in inspector.get_table_names():
        op.drop_table("jobs_get_contact_by_telegram")


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
