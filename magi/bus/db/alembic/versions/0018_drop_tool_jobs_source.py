"""Drop unused ``tool_jobs.source`` column.

Revision ID: 0018_drop_tool_jobs_source
Revises: 0017_split_delivery_job_payload

``tool_jobs.source`` (VARCHAR(32)) was carried on
:class:`magi.bus.guild.runToolJob.RunToolJob` as a "trigger origin"
tag (e.g. ``"llm"`` / ``"agent"``), but no caller ever set it
(``agent/worker.py``'s ``_make_tool_job`` doesn't pass it, so it
stayed ``None`` → persisted as ``""``) and no consumer ever read it
back — neither ``tools/worker.py`` claim logic, nor the API layer,
nor any debug surface. The column was a write-only dead field whose
only value was the empty string.

This migration drops the column. No backfill: every existing row
holds ``""``, and the field has no meaning to preserve. The
downgrade recreates it as ``VARCHAR(32) DEFAULT ''`` to match the
ORM's prior declaration.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_drop_tool_jobs_source"
down_revision: str | Sequence[str] | None = "0017_split_delivery_job_payload"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("tool_jobs")}
    if "source" not in cols:
        return
    with op.batch_alter_table("tool_jobs") as batch:
        batch.drop_column("source")


def downgrade() -> None:
    """Recreate ``tool_jobs.source`` as an empty-string column.

    Best-effort — historical ``source`` values were all ``""`` in
    practice, so there's nothing meaningful to restore. The
    recreated column matches the old ORM declaration.
    """
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("tool_jobs")}
    if "source" in cols:
        return
    with op.batch_alter_table("tool_jobs") as batch:
        batch.add_column(
            sa.Column("source", sa.String(32), nullable=False, server_default="")
        )