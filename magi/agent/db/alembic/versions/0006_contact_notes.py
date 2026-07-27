"""Create ``contact_notes`` — individual notes per contact.

Replaces the single ``contacts.notes`` text column with
a proper one-to-many table so the LLM can add / update /
delete individual facts about a person without rewriting
a monolithic markdown blob.

Existing non-empty ``contacts.notes`` rows are migrated
into one ``contact_notes`` row each (the whole text becomes
a single note — the operator can split in the UI later).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_contact_notes"
# Chained off the new ``0002_admin_role_split`` (the
# collapsed baseline + role/admin split), not the
# pre-collapse ``0005_mcp_servers`` which no longer
# exists. Migrations 0003..0005 were absorbed into
# ``0001_baseline``.
down_revision = "0002_admin_role_split"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "contact_notes" in insp.get_table_names():
        return

    op.create_table(
        "contact_notes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="eve"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_contact_notes_contact_id", "contact_notes", ["contact_id"])

    # Migrate existing notes: one row per non-empty ``contacts.notes``.
    # Each existing note becomes a single ``contact_notes`` row.
    rows = conn.execute(
        sa.text(
            "SELECT id, notes, source, last_seen_at "
            "FROM contacts WHERE notes != ''"
        )
    ).fetchall()
    now = sa.func.datetime("now")
    for row in rows:
        conn.execute(
            sa.text(
                "INSERT INTO contact_notes "
                "(contact_id, note, source, created_at, updated_at) "
                "VALUES (:cid, :note, :source, :now, :now)"
            ),
            {
                "cid": row[0],
                "note": row[1],
                "source": row[2] or "eve",
                "now": now,
            },
        )


def downgrade() -> None:
    op.drop_index("ix_contact_notes_contact_id", table_name="contact_notes")
    op.drop_table("contact_notes")
