"""Bind durable tool jobs to their Tool Catalog snapshot."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0013_tool_job_catalog_snapshot"
down_revision = "0012_tool_catalog"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    columns = _columns("tool_jobs")
    if "tool_source" not in columns:
        op.add_column("tool_jobs", sa.Column("tool_source", sa.String(length=128), nullable=True))
    if "catalog_revision" not in columns:
        op.add_column("tool_jobs", sa.Column("catalog_revision", sa.Integer(), nullable=True))
    if "schema_hash" not in columns:
        op.add_column("tool_jobs", sa.Column("schema_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    # Additive upgrade: leave snapshot columns intact for recovery of jobs
    # created by a newer runtime.
    pass
