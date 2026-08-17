"""Rename the channel-delivery queue to its notification semantics.

The public contract changed from ``DeliveryJob`` to ``DeliveryNotifyJob``.
The migration preserves queued and terminal rows so an upgrade cannot lose an
unsent reply merely because the board was renamed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_rename_delivery_notify"
down_revision = "0002_remove_job_attempts"
branch_labels = None
depends_on = None


_COLUMNS = (
    "job_id, error, status, leased_until, leased_by, created_at, updated_at, "
    "started_at, completed_at, channel, text, conversation_id, contact_id, destination"
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    old_exists = inspector.has_table("delivery_jobs")
    new_exists = inspector.has_table("delivery_notify_jobs")
    if old_exists and not new_exists:
        op.rename_table("delivery_jobs", "delivery_notify_jobs")
        return
    if old_exists and new_exists:
        # ``synchronise_schema`` creates the new declarative table before
        # Alembic runs. Copy first, then remove the retired table.
        op.execute(sa.text(
            f"INSERT INTO delivery_notify_jobs ({_COLUMNS}) "
            f"SELECT {_COLUMNS} FROM delivery_jobs AS old "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM delivery_notify_jobs AS new "
            "WHERE new.job_id = old.job_id"
            ")"
        ))
        op.drop_table("delivery_jobs")


def downgrade() -> None:
    # Dev-only migrations do not promise downgrade compatibility. Keep the
    # old name available for an explicit local rollback without dropping data.
    bind = op.get_bind()
    if sa.inspect(bind).has_table("delivery_notify_jobs"):
        op.rename_table("delivery_notify_jobs", "delivery_jobs")
