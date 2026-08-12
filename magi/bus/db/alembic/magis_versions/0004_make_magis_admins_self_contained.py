"""Make MAGIS administrators independent from local contacts.

Revision ID: 0004_make_magis_admins_self_contained
Revises: 0003_add_a2a_job_boards

``contacts`` belongs to one MAGI-local database and must not model Society
authority.  This migration replaces the old opaque ``contact_id`` reference
with a self-contained MAGIS admin identity and its IM authentication posture.
Existing rows are retained as local-only admins with a deterministic name; an
operator can bind Telegram from the Security settings afterwards.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_make_magis_admins_self_contained"
down_revision: str | Sequence[str] | None = "0003_add_a2a_job_boards"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("magis_admins")}


def upgrade() -> None:
    columns = _columns()
    with op.batch_alter_table("magis_admins") as batch:
        if "name" not in columns:
            batch.add_column(sa.Column("name", sa.String(120), nullable=True))
        if "tgid" not in columns:
            batch.add_column(sa.Column("tgid", sa.BigInteger(), nullable=True))
        if "auth_mode" not in columns:
            batch.add_column(
                sa.Column("auth_mode", sa.String(32), nullable=False, server_default="local_no_2fa")
            )
    if "contact_id" in columns:
        op.execute("UPDATE magis_admins SET name = 'admin-' || contact_id WHERE name IS NULL")
    else:
        op.execute("UPDATE magis_admins SET name = 'admin-' || id WHERE name IS NULL")
    with op.batch_alter_table("magis_admins") as batch:
        batch.alter_column("name", existing_type=sa.String(120), nullable=False)
        if "contact_id" in columns:
            batch.drop_column("contact_id")


def downgrade() -> None:
    columns = _columns()
    with op.batch_alter_table("magis_admins") as batch:
        if "contact_id" not in columns:
            batch.add_column(sa.Column("contact_id", sa.Integer(), nullable=True))
    op.execute("UPDATE magis_admins SET contact_id = id WHERE contact_id IS NULL")
    with op.batch_alter_table("magis_admins") as batch:
        batch.alter_column("contact_id", existing_type=sa.Integer(), nullable=False)
        if "auth_mode" in columns:
            batch.drop_column("auth_mode")
        if "tgid" in columns:
            batch.drop_column("tgid")
        if "name" in columns:
            batch.drop_column("name")
