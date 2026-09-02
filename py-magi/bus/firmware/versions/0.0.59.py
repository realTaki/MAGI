"""Add CallLLM thinking_tokens beside the visible max_tokens budget.

Revision ID: 0.0.59
Revises: 0.0.58
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0.0.59"
down_revision = "0.0.58"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from bus.firmware.versions.schema import firmware_metadata

    bind = op.get_bind()
    inspector = inspect(bind)
    table_name = "jobs_call_llm"
    if table_name in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if "thinking_tokens" not in columns:
            with op.batch_alter_table(table_name) as batch:
                batch.add_column(
                    sa.Column(
                        "thinking_tokens",
                        sa.Integer(),
                        nullable=False,
                        server_default="0",
                    )
                )
    firmware_metadata().create_all(bind=bind)


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
