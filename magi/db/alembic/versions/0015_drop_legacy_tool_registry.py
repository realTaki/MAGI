"""Remove the superseded ``tools`` projection table.

``tool_definitions`` and ``tool_catalog_state`` are the one durable catalog.
No runtime code reads or writes the former execution-registry projection.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0015_drop_legacy_tool_registry"
down_revision = "0014_tool_registry_compat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "tools" in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table("tools")


def downgrade() -> None:
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
