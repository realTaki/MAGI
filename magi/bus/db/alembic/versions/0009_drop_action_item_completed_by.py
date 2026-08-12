"""Drop ``action_items.completed_by_contact_id``.

Revision ID: 0009_drop_action_item_completed_by
Revises: 0008_remove_contact_admin_and_password

Strict per-contact privacy means every completion is stamped by the row's
own owner — the field always equalled ``row.contact_id``, so it carried
no information beyond what the row already exposes.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_drop_action_item_completed_by"
down_revision: str | Sequence[str] | None = "0008_remove_contact_admin_and_password"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("action_items")
    }
    with op.batch_alter_table("action_items") as batch:
        if "completed_by_contact_id" in columns:
            batch.drop_column("completed_by_contact_id")


def downgrade() -> None:
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("action_items")
    }
    with op.batch_alter_table("action_items") as batch:
        if "completed_by_contact_id" not in columns:
            batch.add_column(
                sa.Column(
                    "completed_by_contact_id",
                    sa.Integer(),
                    sa.ForeignKey("contacts.id", ondelete="SET NULL"),
                    nullable=True,
                )
            )
