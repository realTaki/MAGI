"""Add ``leased_by`` + ``leased_until`` columns to ``llm_attempts``.

PR 2 (Phase B) — providers worker introduces a lease-based queue
on top of the existing ``LLMAttempt`` lifecycle row (status
``queued`` ↔ ``claimed``). The two new columns mirror the
``agent_inbox`` / ``tool_jobs`` pattern; both are nullable so
existing audit rows remain untouched.

Revision ID: 0002_add_llm_attempt_lease
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_add_llm_attempt_lease"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_attempts",
        sa.Column("leased_by", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "llm_attempts",
        sa.Column("leased_until", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("llm_attempts", "leased_until")
    op.drop_column("llm_attempts", "leased_by")
