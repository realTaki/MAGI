"""Remove BUS-owned job retry accounting.

Lease expiry makes a row claimable again; it is not a business failure.  The
old ``attempts`` column only supported BUS automatically failing jobs after a
retry budget, so it has no remaining meaning.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_remove_job_attempts"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in (
        "chat_notify_jobs",
        "tool_jobs",
        "llm_jobs",
        "delivery_jobs",
        "change_provider_config_jobs",
        "change_mcp_server_jobs",
        "seed_preset_tasks_jobs",
        "run_task_jobs",
    ):
        if not inspector.has_table(table):
            continue
        if "attempts" in {column["name"] for column in inspector.get_columns(table)}:
            with op.batch_alter_table(table) as batch:
                batch.drop_column("attempts")


def downgrade() -> None:
    for table in (
        "chat_notify_jobs",
        "tool_jobs",
        "llm_jobs",
        "delivery_jobs",
        "change_provider_config_jobs",
        "change_mcp_server_jobs",
        "seed_preset_tasks_jobs",
        "run_task_jobs",
    ):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
