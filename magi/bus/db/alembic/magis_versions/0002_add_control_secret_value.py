"""Add ``control_secrets.secret_value`` column for in-DB proxy signing.

Revision ID: 0002_add_control_secret_value

Before this migration the control secret lived only on disk at
``<host_workspace>/MAGI_Societies/<magis>/control/control-secret``,
read by the WebUI / Runtime launcher scripts. That made the secret
easy to lose in DB-only backup flows and required every launcher
script to know the file path.

The new ``secret_value`` column lets ``init_first_magi`` persist
the raw bytes alongside the existing ``secret_hash`` / ``salt``
audit fields. Reads prefer the DB column and fall back to the file
when the column is NULL (legacy rows).

Both scopes (local + magis) carry the same revision because the
table lives in the MAGIS store; the local store never has one.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0002_add_control_secret_value"
down_revision: str | Sequence[str] | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the ``secret_value`` column to ``control_secrets``.

    Nullable so pre-migration rows survive untouched; their lookups
    fall back to the bootstrap file at the launcher. SQLite supports
    ``ALTER TABLE ADD COLUMN`` natively; on PostgreSQL this is
    equally straightforward.
    """
    with op.batch_alter_table("control_secrets") as batch_op:
        batch_op.add_column(
            sa.Column("secret_value", sa.LargeBinary(), nullable=True),
        )


def downgrade() -> None:
    """Drop the column on the way down. Data loss is acceptable in dev."""
    with op.batch_alter_table("control_secrets") as batch_op:
        batch_op.drop_column("secret_value")