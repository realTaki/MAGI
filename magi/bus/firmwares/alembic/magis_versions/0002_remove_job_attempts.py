"""Remove BUS-owned A2A retry accounting."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_remove_job_attempts"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in ("a2a_request_jobs", "a2a_notify_jobs"):
        if not inspector.has_table(table):
            continue
        if "attempts" in {column["name"] for column in inspector.get_columns(table)}:
            with op.batch_alter_table(table) as batch:
                batch.drop_column("attempts")


def downgrade() -> None:
    for table in ("a2a_request_jobs", "a2a_notify_jobs"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
