"""Add ``hook_plugin_configs`` table — persistent hook enablement.

Drives whether a hook plugin is registered with ``bus.hooks``
at boot.  The WebUI Hooks knowledge page and ``magi hook``
CLI both write here.  Plugins cannot self-register.

Revision ID: 0004_hook_plugin_configs
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0004_hook_plugin_configs"
down_revision = "0003_hook_evaluations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hook_plugin_configs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("hook_id", sa.String(length=128), nullable=False),
        sa.Column("hook_version", sa.String(length=32), nullable=False),
        sa.Column("module_path", sa.String(length=256), nullable=False),
        sa.Column("class_name", sa.String(length=128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("required_scopes", sa.JSON(), nullable=False),
        sa.Column("timeout_ms", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("failure_mode", sa.String(length=16), nullable=False),
        sa.Column("hook_points", sa.JSON(), nullable=False),
        sa.Column("init_kwargs_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("hook_id", name="uq_hook_plugin_configs_hook_id"),
    )


def downgrade() -> None:
    op.drop_table("hook_plugin_configs")
