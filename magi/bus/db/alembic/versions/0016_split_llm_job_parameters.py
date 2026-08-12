"""Split ``llm_jobs.parameters`` (JSON blob) into individual columns.

Revision ID: 0016_split_llm_job_parameters
Revises: 0015_split_chat_job_payload

Before this migration, :class:`magi.bus.guild.callLLMJob.CallLLMJob`
carried a single ``parameters: dict[str, Any]`` attribute, persisted as
a JSON column on the ``llm_jobs`` table. The dict was a black box:
nobody — not the producer, not the consumer, not the reader — could
tell which keys it should contain without reading the call site, and
in practice the keyset was a five-field union
(``contact_id`` / ``conversation_id`` / ``channel`` /
``caller_role`` / ``phase``) that all three producers duplicated
verbatim.

After this migration each context field is its own column, and
:class:`CallLLMJob` is a typed dataclass with one attribute per
field. The Python API, the ORM mapping, and the DB schema all line
up — same shape as the ``chat_jobs`` refactor (0015).

Backfill: for every existing row, copy the value of each known
``parameters`` key into the corresponding new column. Rows that
predate the typed fields (no ``parameters`` row, or a row with
missing keys) end up with the column default (empty string /
``None``).

After backfill, the ``parameters`` column is dropped. Any future
producer / consumer that wants a field has to add it to
:class:`CallLLMJob` *and* to this migration — no more silent key
drops.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_split_llm_job_parameters"
down_revision: str | Sequence[str] | None = "0015_split_chat_job_payload"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Field mapping: (new column name, parameters key, SQL type, nullable). Mirrors
# ``_LLMJobRow`` in :mod:`magi.bus.guild.callLLMJob`. ``nullable=False`` lines
# up with the ORM (``conversation_id`` / ``channel`` are always populated at
# publish time) and matches the ``chat_jobs`` precedent (migration 0015).
_BACKFILL_FIELDS: tuple[tuple[str, str, type[sa.types.TypeEngine], bool], ...] = (
    ("contact_id", "contact_id", sa.Integer(), True),
    ("conversation_id", "conversation_id", sa.String(128), False),
    ("channel", "channel", sa.String(16), False),
    ("caller_role", "caller_role", sa.String(16), True),
    ("phase", "phase", sa.String(32), True),
)


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("llm_jobs")}
    has_parameters = "parameters" in cols

    with op.batch_alter_table("llm_jobs") as batch:
        for col_name, _param_key, sql_type, nullable in _BACKFILL_FIELDS:
            if col_name not in cols:
                # NOT NULL columns get a server_default so pre-migration
                # rows backfill to ``""`` rather than failing the
                # add-column (SQLite refuses NOT NULL without DEFAULT
                # on ALTER TABLE). The ORM still declares them
                # ``default=""`` for fresh schemas.
                kwargs: dict[str, object] = {"nullable": nullable}
                if not nullable:
                    kwargs["server_default"] = ""
                batch.add_column(sa.Column(col_name, sql_type, **kwargs))

    # Backfill from the existing parameters JSON. Some drivers
    # return the JSON as a string, some as a dict — normalise.
    if has_parameters:
        rows = bind.execute(
            sa.text(
                "SELECT id, parameters FROM llm_jobs WHERE parameters IS NOT NULL"
            )
        ).fetchall()
        for row_id, raw in rows:
            if not raw:
                continue
            params = raw if isinstance(raw, dict) else _safe_load(raw)
            if not isinstance(params, dict):
                continue
            # ``COALESCE(..., '')`` so missing keys don't violate the
            # NOT NULL constraint on ``conversation_id`` / ``channel``.
            values: dict[str, object] = {
                col_name: params.get(key)
                for col_name, key, _, _ in _BACKFILL_FIELDS
            }
            assignments = ", ".join(
                # Two single quotes for the SQL empty-string literal;
                # NULL for nullable columns (the column's default
                # would override anyway).
                f"{n} = COALESCE(:{n}, '')" if not nullable
                else f"{n} = COALESCE(:{n}, NULL)"
                for n, _, _, nullable in _BACKFILL_FIELDS
            )
            bind.execute(
                sa.text(
                    f"UPDATE llm_jobs SET {assignments} WHERE id = :id"
                ),
                {**values, "id": row_id},
            )

    if has_parameters:
        with op.batch_alter_table("llm_jobs") as batch:
            batch.drop_column("parameters")


def downgrade() -> None:
    """Recreate the ``parameters`` JSON column from the typed fields.

    Best-effort — strings are kept as-is, non-serialisable values
    land as JSON ``null``. Old data is preserved exactly; new
    column-only fields that don't fit in the JSON shape are
    dropped.
    """
    with op.batch_alter_table("llm_jobs") as batch:
        batch.add_column(
            sa.Column("parameters", sa.JSON(), nullable=True)
        )

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, contact_id, conversation_id, channel, "
            "caller_role, phase FROM llm_jobs"
        )
    ).fetchall()
    for row in rows:
        parameters = {
            "contact_id": row.contact_id,
            "conversation_id": row.conversation_id,
            "channel": row.channel,
            "caller_role": row.caller_role,
            "phase": row.phase,
        }
        bind.execute(
            sa.text("UPDATE llm_jobs SET parameters = :parameters WHERE id = :id"),
            {"parameters": json.dumps(parameters), "id": row.id},
        )

    with op.batch_alter_table("llm_jobs") as batch:
        for col_name, _key, _sql_type, _nullable in _BACKFILL_FIELDS:
            batch.drop_column(col_name)


def _safe_load(raw: str) -> object:
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None
