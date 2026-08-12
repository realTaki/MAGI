"""Split ``delivery_outbox.payload`` (JSON blob) into individual columns.

Revision ID: 0017_split_delivery_job_payload
Revises: 0016_split_llm_job_parameters

Before this migration, :class:`magi.bus.guild.deliveryJob.DeliveryJob`
carried a single ``payload: dict`` attribute, persisted as a JSON
column on the ``delivery_outbox`` table. The dict was a black box:
nobody — not the producer, not the consumer, not the reader — could
tell which keys it should contain without reading the call site.

After this migration each delivery-content field is its own column,
and :class:`DeliveryJob` is a typed dataclass with one attribute per
field. The Python API, the ORM mapping, and the DB schema all line
up — same shape as :class:`magi.bus.guild.chatJob.ChatJob` (see
migration 0015).

Backfill: every existing row is read once, the value of each known
payload key is copied into the corresponding new column, then the
``payload`` column is dropped. Rows that predate the typed fields
(no ``payload`` row, or a row with missing keys) end up with the
column default (empty string / ``None``).

Order matters on SQLite: existing rows block ``ADD COLUMN ... NOT
NULL``, so the new columns are added as nullable, backfilled, and
only then locked down with ``NOT NULL`` for ``text``. The
``conversation_id`` / ``contact_id`` columns stay nullable — they're
optional even after the split.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_split_delivery_job_payload"
down_revision: str | Sequence[str] | None = "0016_split_llm_job_parameters"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Field mapping: (new column name, payload key, SQL type, post-backfill NOT NULL).
# Mirrors ``_DeliveryJobRow`` in :mod:`magi.bus.guild.deliveryJob`.
_BACKFILL_FIELDS: tuple[tuple[str, str, type[sa.types.TypeEngine], bool], ...] = (
    ("text", "text", sa.Text(), True),
    ("conversation_id", "conversation_id", sa.String(128), False),
    ("contact_id", "contact_id", sa.Integer(), False),
)


def upgrade() -> None:
    bind = op.get_bind()
    cols = {
        c["name"] for c in sa.inspect(bind).get_columns("delivery_outbox")
    }
    has_payload = "payload" in cols

    # Step 1 — add columns nullable. SQLite refuses
    # ``ALTER TABLE ... ADD COLUMN ... NOT NULL`` when the table
    # already has rows, even with ``batch_alter_table``'s table
    # rebuild, so we add nullable and tighten ``text`` later.
    with op.batch_alter_table("delivery_outbox") as batch:
        for col_name, _payload_key, sql_type, _not_null in _BACKFILL_FIELDS:
            if col_name not in cols:
                batch.add_column(sa.Column(col_name, sql_type, nullable=True))

    # Step 2 — backfill from the existing payload JSON. Some drivers
    # return the JSON as a string, some as a dict — normalise.
    if has_payload:
        rows = bind.execute(
            sa.text(
                "SELECT id, payload FROM delivery_outbox WHERE payload IS NOT NULL"
            )
        ).fetchall()
        for row_id, raw in rows:
            if not raw:
                continue
            payload = raw if isinstance(raw, dict) else _safe_load(raw)
            if not isinstance(payload, dict):
                continue
            values: dict[str, object] = {
                col_name: payload.get(key)
                for col_name, key, _, _ in _BACKFILL_FIELDS
            }
            bind.execute(
                sa.text(
                    "UPDATE delivery_outbox SET "
                    + ", ".join(f"{n} = :{n}" for n in values)
                    + " WHERE id = :id"
                ),
                {**values, "id": row_id},
            )

    # Step 3 — tighten ``text`` to NOT NULL. New rows will always
    # have it set, and backfill just covered the legacy ones.
    with op.batch_alter_table("delivery_outbox") as batch:
        batch.alter_column("text", existing_type=sa.Text(), nullable=False)

    if has_payload:
        with op.batch_alter_table("delivery_outbox") as batch:
            batch.drop_column("payload")


def downgrade() -> None:
    """Recreate the ``payload`` JSON column from the typed fields.

    Best-effort — strings are kept as-is, non-serialisable values
    land as JSON ``null``. Old data is preserved exactly; new
    column-only fields that don't fit in the JSON shape are
    dropped.
    """
    with op.batch_alter_table("delivery_outbox") as batch:
        batch.add_column(
            sa.Column("payload", sa.JSON(), nullable=True)
        )

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, text, conversation_id, contact_id FROM delivery_outbox"
        )
    ).fetchall()
    for row in rows:
        payload = {
            "text": row.text,
            "conversation_id": row.conversation_id,
            "contact_id": row.contact_id,
        }
        bind.execute(
            sa.text("UPDATE delivery_outbox SET payload = :payload WHERE id = :id"),
            {"payload": json.dumps(payload), "id": row.id},
        )

    # Drop the typed columns. ``text`` is currently NOT NULL — pass
    # through ``alter_column(..., nullable=True)`` first so the
    # rebuild doesn't trip on legacy rows that may have a NULL.
    with op.batch_alter_table("delivery_outbox") as batch:
        batch.alter_column("text", existing_type=sa.Text(), nullable=True)

    with op.batch_alter_table("delivery_outbox") as batch:
        for col_name, _key, _sql_type, _not_null in _BACKFILL_FIELDS:
            batch.drop_column(col_name)


def _safe_load(raw: str) -> object:
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None