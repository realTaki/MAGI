"""Remove local admin and password state from contacts.

Revision ID: 0008_remove_contact_admin_and_password
Revises: 0007_add_magis_admin_projection

Administrator authority and authentication live in the MAGIS shared store.
``contacts`` remains a local people/projection table; it must not retain a
second authority model or password credential material.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_remove_contact_admin_and_password"
down_revision: str | Sequence[str] | None = "0007_add_magis_admin_projection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("contacts")}
    with op.batch_alter_table("contacts") as batch:
        if "password_hash" in columns:
            batch.drop_column("password_hash")
        if "admin" in columns:
            batch.drop_column("admin")


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("contacts")}
    with op.batch_alter_table("contacts") as batch:
        if "admin" not in columns:
            batch.add_column(
                sa.Column("admin", sa.Boolean(), nullable=False, server_default=sa.false())
            )
        if "password_hash" not in columns:
            batch.add_column(sa.Column("password_hash", sa.String(255), nullable=True))
