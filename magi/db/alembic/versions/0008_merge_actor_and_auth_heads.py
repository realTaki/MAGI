"""Merge actor-runtime and auth credential migration branches.

Revision ID: 0008_merge_actor_and_auth_heads
Revises: 0007_actor_runtime_completion, 0006_auth_credentials
"""

from __future__ import annotations

from alembic import op


revision = "0008_merge_actor_and_auth_heads"
down_revision = ("0007_actor_runtime_completion", "0006_auth_credentials")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Merge-only revision: both parent branches have already applied their
    # schema changes independently.
    pass


def downgrade() -> None:
    # Alembic will split to the two parent heads; no DDL belongs here.
    pass
