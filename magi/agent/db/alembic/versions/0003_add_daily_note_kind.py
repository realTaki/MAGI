"""Add ``kind`` / ``note_date`` to ``contact_notes`` + daily presets.

Two shapes of contact-attached memory now live in
``contact_notes``:

- ``kind='permanent'`` — the long-arc fact stream
  (each ``add_contact_note`` call inserts a new row).
- ``kind='daily'`` — the short-arc daily log
  (``update_daily_note`` appends to a single row keyed by
  ``(contact_id, note_date)``).

``note_date`` is naive UTC midnight of the day the row
belongs to. NULL on permanent rows; non-null on daily rows.

This revision:

1. Adds the two columns + a partial unique index
   (``ux_contact_notes_daily``) so a contact has at most one
   daily row per date.
2. Adds a covering index ``ix_contact_notes_uid_kind_date``
   for the morning/night report's "today's daily note for
   uid X" lookup.
3. Backfills the two new system-level presets
   (``morning_brief`` / ``night_summary``) for every
   existing assigned ``Contact`` so the upgrade lands
   operators with a working daily report immediately.
   The backfill is idempotent — re-running this revision
   adds at most one row per (contact, preset_id) pair.

Idempotent guards on every schema change so re-running on
a DB that already has the columns / indexes is a no-op.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0003_add_daily_note_kind"
down_revision = "0002_drop_contact_provider_api_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("contact_notes")}
    if "kind" not in cols:
        op.add_column(
            "contact_notes",
            sa.Column(
                "kind",
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'permanent'"),
            ),
        )
    if "note_date" not in cols:
        op.add_column(
            "contact_notes",
            sa.Column("note_date", sa.DateTime(), nullable=True),
        )

    # Indexes are idempotent at the SQLAlchemy level — the
    # ``if_not_exists=True`` flag handles re-runs without
    # failing the migration.
    inspector = sa.inspect(bind)
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("contact_notes")}

    if "ix_contact_notes_uid_kind_date" not in existing_indexes:
        op.create_index(
            "ix_contact_notes_uid_kind_date",
            "contact_notes",
            ["contact_id", "kind", "note_date"],
            unique=False,
        )

    # SQLite supports partial indexes via the ``sqlite_where``
    # kwarg on ``create_index``. The partial unique index
    # lets multiple permanent rows (note_date IS NULL) per
    # contact per day co-exist while still allowing only one
    # daily row per (contact_id, note_date) pair.
    if "ux_contact_notes_daily" not in existing_indexes:
        op.create_index(
            "ux_contact_notes_daily",
            "contact_notes",
            ["contact_id", "note_date"],
            unique=True,
            sqlite_where=sa.text("kind = 'daily'"),
        )

    # --- Backfill: seed the two new system-level presets for
    # every existing assigned Contact. The seed helper's
    # per-preset existence check makes this idempotent — a
    # DB that's been upgraded twice gets the new presets on
    # the first run only.
    from sqlalchemy import select

    from magi.agent.db import Contact, open_session
    from magi.agent.proactive.presets import seed_presets_for_contact

    with open_session() as session:
        assigned_ids = [
            cid for (cid,) in session.execute(
                select(Contact.id).where(Contact.role == "assigned")
            ).all()
        ]
        # Per-contact commit point — each call seeds the
        # helper's own existence check, so we don't need to
        # batch across contacts.
        for cid in assigned_ids:
            try:
                seed_presets_for_contact(session, cid)
                session.commit()
            except Exception as exc:  # noqa: BLE001 — defensive
                # Single-contact failures (e.g. legacy rows
                # with NULL required columns) shouldn't
                # block the migration. Log via print since
                # the alembic logger isn't bound here.
                print(
                    f"0003 backfill: skipping contact {cid}: {exc}",
                )
                session.rollback()


def downgrade() -> None:
    # Drop the indexes first (FK-free so safe to drop in any
    # order) then the columns. SQLite can't easily drop a
    # column in older builds; the operational downgrade
    # path is "wipe + rebaseline", not "revert this
    # revision", so we keep the column drop in the
    # downgrade for completeness.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("contact_notes")}
    if "ux_contact_notes_daily" in existing_indexes:
        op.drop_index("ux_contact_notes_daily", table_name="contact_notes")
    if "ix_contact_notes_uid_kind_date" in existing_indexes:
        op.drop_index("ix_contact_notes_uid_kind_date", table_name="contact_notes")
    cols = {c["name"] for c in sa.inspect(bind).get_columns("contact_notes")}
    if "note_date" in cols:
        op.drop_column("contact_notes", "note_date")
    if "kind" in cols:
        op.drop_column("contact_notes", "kind")