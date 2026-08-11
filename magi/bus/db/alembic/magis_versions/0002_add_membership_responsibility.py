"""Add public collaboration responsibilities to MAGIS memberships.

The field is intentionally MAGIS-scoped: it is the public, operator-managed
description an Agent uses to choose collaborators, not a private node prompt.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_add_membership_responsibility"
down_revision: str | Sequence[str] | None = "0001_magis_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(column: str) -> bool:
    return any(
        item["name"] == column
        for item in sa.inspect(op.get_bind()).get_columns("magis_memberships")
    )


def upgrade() -> None:
    if not _has_column("responsibility"):
        op.add_column(
            "magis_memberships",
            sa.Column("responsibility", sa.Text(), nullable=False, server_default=""),
        )


def downgrade() -> None:
    if _has_column("responsibility"):
        op.drop_column("magis_memberships", "responsibility")
