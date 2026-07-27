"""Drop ``contacts.provider`` / ``contacts.api_key``.

LLM credentials live on the ``magis`` table now (one
MAGI runtime = one provider+api_key pair, configured
once at boot). The ``contacts`` copy was a v0
convenience that the codebase's own comment flagged
for removal; the F1 follow-up is now.

``token_usage`` still records per-contact (the uid
column carries who triggered the call) but the
``provider`` string in that row is the MAGI's provider,
not a per-contact one.

Idempotent for any pre-existing DB that already has the
columns (dev mode after the role/admin split).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0002_drop_contact_provider_api_key"
down_revision = "0006_contact_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("contacts")}
    if "provider" in cols:
        op.drop_column("contacts", "provider")
    if "api_key" in cols:
        op.drop_column("contacts", "api_key")


def downgrade() -> None:
    # Re-add the columns. Best-effort — pre-removal data
    # is gone (drop_column is destructive). Downgrading is
    # only useful for a fresh-DB inspect test.
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("contacts")}
    if "provider" not in cols:
        op.add_column(
            "contacts",
            sa.Column("provider", sa.String(length=32), nullable=True),
        )
    if "api_key" not in cols:
        op.add_column(
            "contacts",
            sa.Column("api_key", sa.String(length=512), nullable=True),
        )