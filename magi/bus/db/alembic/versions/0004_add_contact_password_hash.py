"""Add the local ``contacts.password_hash`` column.

Revision ID: 0004_add_contact_password_hash
Revises: 0003_rename_a2a_invocation_id_and_table

Password credentials used to be represented by a MAGIS-scoped table.  They
are now owned directly by the MAGI-local contact that authenticates with the
password.  Fresh stores get this nullable column from SQLAlchemy metadata;
this migration upgrades existing local SQLite stores in place.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_add_contact_password_hash"
down_revision: str | Sequence[str] | None = "0003_rename_a2a_invocation_id_and_table"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(column: str) -> bool:
    return any(item["name"] == column for item in sa.inspect(op.get_bind()).get_columns("contacts"))


def upgrade() -> None:
    if not _has_column("password_hash"):
        op.add_column("contacts", sa.Column("password_hash", sa.String(255), nullable=True))


def downgrade() -> None:
    if _has_column("password_hash"):
        op.drop_column("contacts", "password_hash")
