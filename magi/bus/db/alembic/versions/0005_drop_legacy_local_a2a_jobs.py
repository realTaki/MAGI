"""Drop the obsolete MAGI-local HTTP A2A outbox.

A2A now resides in the shared MAGIS request/notify boards.  The old local
``a2a_jobs`` table belonged to the removed HTTP delivery path and must not be
retained as a compatibility queue.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_drop_legacy_local_a2a_jobs"
down_revision: str | Sequence[str] | None = "0004_add_contact_password_hash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "a2a_jobs" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("a2a_jobs")


def downgrade() -> None:
    # The obsolete transport is intentionally not recreated.
    return None
