"""Add the local projection link for MAGIS administrators.

Revision ID: 0007_add_magis_admin_projection
Revises: 0006_rename_contacts_telegram_id_to_tgid

MAGIS admins are shared identities.  A runtime-local Contact is their local
projection for conversations, action items, and other local ownership; this
nullable opaque ID is the bridge and is intentionally not a cross-database FK.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_add_magis_admin_projection"
down_revision: str | Sequence[str] | None = "0006_rename_contacts_telegram_id_to_tgid"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("contacts")}
    if "magis_admin_id" not in columns:
        with op.batch_alter_table("contacts") as batch:
            batch.add_column(sa.Column("magis_admin_id", sa.BigInteger(), nullable=True, unique=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("contacts")}
    if "magis_admin_id" in columns:
        with op.batch_alter_table("contacts") as batch:
            batch.drop_column("magis_admin_id")
