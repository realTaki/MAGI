"""Add ``chat_conversations.summary`` (cumulative compaction summary).

Revision ID: 0010_add_conversation_summary
Revises: 0009_drop_action_item_completed_by

Nullable Text — populated by auto-compaction; ``None`` means "never
compacted". Reads/writes go through `ConversationBook.set_summary` and
`build_messages_from_conversation` (which prepends the summary to the
loaded history so the LLM sees prior context on every turn).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_add_conversation_summary"
down_revision: str | Sequence[str] | None = "0009_drop_action_item_completed_by"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("chat_conversations")}
    with op.batch_alter_table("chat_conversations") as batch:
        if "summary" not in cols:
            batch.add_column(sa.Column("summary", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("chat_conversations") as batch:
        batch.drop_column("summary")