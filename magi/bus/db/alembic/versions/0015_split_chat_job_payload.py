"""Split ``chat_jobs.payload`` (JSON blob) into individual columns.

Revision ID: 0015_split_chat_job_payload
Revises: 0014_convert_task_time_columns_to_datetime

Before this migration, :class:`magi.bus.guild.chatJob.ChatJob` carried a
single ``payload: dict[str, Any]`` attribute, persisted as a JSON
column on the ``chat_jobs`` table. The dict was a black box: nobody
— not the producer, not the consumer, not the reader — could tell
which keys it should contain without reading the call site.

After this migration each turn-input field is its own column, and
:class:`ChatJob` is a typed dataclass with one attribute per field.
The Python API, the ORM mapping, and the DB schema all line up.

Backfill: for every existing row, copy the value of each known
payload key into the corresponding new column. Rows that predate
the typed fields (no ``payload`` row, or a row with missing keys)
end up with the column default (empty string / ``None``).

After backfill, the ``payload`` column is dropped. Any future
producer / consumer that wants a field has to add it to
:class:`ChatJob` *and* to this migration — no more silent key drops.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_split_chat_job_payload"
down_revision: str | Sequence[str] | None = "0014_convert_task_time_columns_to_datetime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Field mapping: (new column name, payload key, SQL type). Mirrors
# ``_ChatJobRow`` in :mod:`magi.bus.guild.chatJob`.
_BACKFILL_FIELDS: tuple[tuple[str, str, type[sa.types.TypeEngine]], ...] = (
    ("text", "text", sa.Text()),
    ("channel", "channel", sa.String(16)),
    ("contact_id", "contact_id", sa.Integer()),
    ("caller_role", "caller_role", sa.String(16)),
    ("chat_id", "chat_id", sa.String(64)),
    ("tg_message_id", "tg_message_id", sa.Integer()),
    ("kind", "kind", sa.String(32)),
    ("task_id", "task_id", sa.String(64)),
    ("manual", "manual", sa.Boolean()),
)


def upgrade() -> None:
    bind = op.get_bind()
    cols = {
        c["name"] for c in sa.inspect(bind).get_columns("chat_jobs")
    }
    has_payload = "payload" in cols

    with op.batch_alter_table("chat_jobs") as batch:
        for col_name, _payload_key, sql_type in _BACKFILL_FIELDS:
            if col_name not in cols:
                nullable = col_name not in ("text", "channel")
                batch.add_column(
                    sa.Column(col_name, sql_type, nullable=nullable)
                )

    # Backfill from the existing payload JSON. Some drivers return
    # the JSON as a string, some as a dict — normalise.
    if has_payload:
        rows = bind.execute(
            sa.text(
                "SELECT id, payload FROM chat_jobs WHERE payload IS NOT NULL"
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
                for col_name, key, _ in _BACKFILL_FIELDS
            }
            bind.execute(
                sa.text(
                    "UPDATE chat_jobs SET "
                    + ", ".join(f"{n} = :{n}" for n in values)
                    + " WHERE id = :id"
                ),
                {**values, "id": row_id},
            )

    if has_payload:
        with op.batch_alter_table("chat_jobs") as batch:
            batch.drop_column("payload")


def downgrade() -> None:
    """Recreate the ``payload`` JSON column from the typed fields.

    Best-effort — strings are kept as-is, non-serialisable values
    land as JSON ``null``. Old data is preserved exactly; new
    column-only fields that don't fit in the JSON shape are
    dropped.
    """
    with op.batch_alter_table("chat_jobs") as batch:
        batch.add_column(
            sa.Column("payload", sa.JSON(), nullable=True)
        )

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, text, channel, contact_id, caller_role, chat_id, "
            "tg_message_id, kind, task_id, manual FROM chat_jobs"
        )
    ).fetchall()
    for row in rows:
        payload = {
            "text": row.text,
            "channel": row.channel,
            "contact_id": row.contact_id,
            "caller_role": row.caller_role,
            "chat_id": row.chat_id,
            "tg_message_id": row.tg_message_id,
            "kind": row.kind,
            "task_id": row.task_id,
            "manual": row.manual,
        }
        bind.execute(
            sa.text("UPDATE chat_jobs SET payload = :payload WHERE id = :id"),
            {"payload": json.dumps(payload), "id": row.id},
        )

    with op.batch_alter_table("chat_jobs") as batch:
        for col_name, _key, sql_type in _BACKFILL_FIELDS:
            batch.drop_column(col_name)


def _safe_load(raw: str) -> object:
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None