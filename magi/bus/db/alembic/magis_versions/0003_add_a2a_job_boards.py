"""Record the MAGIS-shared A2A request and notify board schema.

Fresh stores receive the tables from BUS metadata before Alembic runs.  This
revision establishes the durable shared-A2A schema as a first-class MAGIS
revision for existing deployments as well.
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0003_add_a2a_job_boards"
down_revision: str | Sequence[str] | None = "0002_add_membership_responsibility"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ``synchronise_schema`` materialises declarative tables before this
    # migration, preserving the project-wide additive schema convention.
    return None


def downgrade() -> None:
    # A2A transcripts are durable audit data; no destructive downgrade.
    return None
