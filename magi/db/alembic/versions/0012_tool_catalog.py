"""Add the durable, BUS-owned Tool Catalog."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0012_tool_catalog"
down_revision = "0011_agent_run_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tool_definitions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("input_schema_json", sa.JSON(), nullable=False),
        sa.Column("allowed_roles_json", sa.JSON(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("implementation_version", sa.String(length=128), nullable=True),
        sa.Column("schema_hash", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("source", "name", name="uq_tool_definitions_source_name"),
    )
    op.create_index("ix_tool_definitions_enabled", "tool_definitions", ["enabled", "source", "name"])
    op.create_table(
        "tool_catalog_state",
        sa.Column("singleton_key", sa.String(length=64), primary_key=True),
        sa.Column("current_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("tool_catalog_state")
    op.drop_index("ix_tool_definitions_enabled", table_name="tool_definitions")
    op.drop_table("tool_definitions")
