"""Add desired/observed EVE runtime state.

The initial lifecycle control plane keeps its state in Adam's database while
the actual workload is managed by the separately privileged orchestrator.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0007_eve_runtimes"
# The repository's live head is ``0003_add_daily_note_kind``.  Despite its
# numeric-looking name it follows the contact-key migration, which follows
# ``0006_contact_notes``; use the actual graph rather than lexical numbering.
down_revision = "0003_add_daily_note_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eve_runtimes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("magi_id", sa.Integer(), nullable=False),
        sa.Column("desired_state", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("observed_state", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("namespace", sa.String(length=63), nullable=True),
        sa.Column("deployment_name", sa.String(length=63), nullable=True),
        sa.Column("workspace_claim_name", sa.String(length=63), nullable=True),
        sa.Column("credential_secret_name", sa.String(length=63), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["magi_id"], ["magis.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("magi_id"),
    )
    op.create_index("ix_eve_runtimes_magi_id", "eve_runtimes", ["magi_id"])


def downgrade() -> None:
    op.drop_index("ix_eve_runtimes_magi_id", table_name="eve_runtimes")
    op.drop_table("eve_runtimes")
