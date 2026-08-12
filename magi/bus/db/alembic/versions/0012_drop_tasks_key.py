"""Drop ``tasks.key`` — the proactive-only stable preset identifier.

Revision ID: 0012_drop_tasks_key
Revises: 0011_drop_task_run_tokens

The ``key`` column was never populated by the active seeding path
(:func:`magi.proactive.preset_tasks.handle_seed_job` only stamps
``source=PROACTIVE`` and never passes a ``key`` kwarg into
``TaskBook.add``), and the WebUI discriminator that consumed it
(``preset_key IS NOT NULL``) was never wired to a backend that
actually surfaced the column. With ``source`` already splitting
preset vs. user tasks at the row level, the column carried no
information beyond what ``source`` already exposed. Drop it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_drop_tasks_key"
down_revision: str | Sequence[str] | None = "0011_drop_task_run_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("tasks")
    }
    with op.batch_alter_table("tasks") as batch:
        if "key" in columns:
            batch.drop_column("key")


def downgrade() -> None:
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("tasks")
    }
    with op.batch_alter_table("tasks") as batch:
        if "key" not in columns:
            batch.add_column(
                sa.Column("key", sa.String(length=64), nullable=True)
            )
