"""Baseline the MAGIS-shared store managed by BUS metadata.

The normal synchronisation flow creates missing MAGIS tables from the
declarative metadata, then this revision records the initial Alembic head.
Future MAGIS-only changes must be added after this revision instead of being
hidden in application startup code.
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0001_magis_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """The metadata synchronisation step creates the baseline tables."""


def downgrade() -> None:
    """The baseline deliberately has no destructive downgrade."""
