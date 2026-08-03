"""Historical migration for the superseded ``tools`` projection table.

The next migration drops this table. The revision is retained solely so an
already-upgraded installation has a valid Alembic history.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0014_tool_registry_compat"
down_revision = "0013_tool_job_catalog_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "tools" in existing:
        return
    op.create_table(
        "tools",
        sa.Column("name", sa.String(length=128), primary_key=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("input_schema", sa.JSON(), nullable=False),
        sa.Column("allowed_roles", sa.JSON(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="builtin"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    # Retain compatibility data for an older WebUI process during rollback.
    pass
