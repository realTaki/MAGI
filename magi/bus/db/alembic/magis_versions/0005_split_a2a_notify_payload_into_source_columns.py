"""Split ``a2a_notify_jobs.payload`` into explicit ``source_*`` columns.

Revision ID: 0005_split_a2a_notify_payload_into_source_columns
Revises: 0004_make_magis_admins_self_contained
Create Date: 2026-08-12 00:00:00.000000

``A2ANotifyJob`` historically carried an opaque ``payload: dict`` JSON
column. Producers (``AgentWorker._split_tools``) only ever stuffed two
keys — ``source_channel`` and ``source_conversation_id`` — and nothing
on the consumer side ever read them. The opaque dict forced every
producer / consumer to coordinate on an undocumented schema that
magi's type checkers could not see.

This revision replaces the column with two first-class
``source_channel`` / ``source_conversation_id`` columns, mirroring the
shape of the new :class:`magi.bus.guild.a2aJob.A2ANotifyJob`
dataclass. Any existing rows whose ``payload`` happens to be a JSON
object with one or both keys are migrated forward; unknown keys are
dropped (no producer / consumer pair ever referenced them).

DDL strategy
============

``a2a_notify_jobs`` carries ``ON DELETE CASCADE`` foreign keys to
``magis_memberships`` (``source_magi_id`` / ``target_magi_id``).
Avoiding :func:`op.batch_alter_table` mirrors
``0014_convert_task_time_columns_to_datetime``: SQLite's
batch-alter copy-and-recreate path can briefly drop the parent's
``ON DELETE CASCADE`` invariant and silently cascade through
``magis_memberships``. Instead the migration uses three native
``ALTER TABLE`` statements (``ADD COLUMN`` with ``DEFAULT``, ``DROP
COLUMN``, ``RENAME COLUMN``), wrapped in ``PRAGMA foreign_keys=OFF``
… ``PRAGMA foreign_keys=ON`` + ``PRAGMA foreign_key_check`` just
like ``0014`` does for ``tasks → task_runs``. SQLite 3.35+ supports
``DROP COLUMN`` natively; the project's Python runtime enforces
``sqlite_version >= 3.36`` elsewhere, so this path is safe.

PostgreSQL is handled by the same Alembic op statements because
``ADD COLUMN ... DEFAULT ''`` (dropped at the NOT NULL ALTER time)
plus plain ``DROP COLUMN`` is fully supported there too. The
per-row payload extraction below uses JSON only when the value
parses cleanly, so dialect-level JSON quirks are irrelevant.

Data migration
==============

For every existing row we attempt to parse ``payload`` as JSON. If
the result is a dict, we copy ``source_channel`` / ``source_conversation_id``
into the new columns and clear ``payload`` via ``DROP COLUMN``.
Anything else (NULL, scalar, malformed) gets default-empty values,
matching the column defaults.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, UTC
import json
import logging

import sqlalchemy as sa
from alembic import op

revision: str = "0005_split_a2a_notify_payload_into_source_columns"
down_revision: str | Sequence[str] | None = "0004_make_magis_admins_self_contained"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_log = logging.getLogger(__name__)

_TABLE = "a2a_notify_jobs"
_SOURCE_CHANNEL_COL = "source_channel"
_SOURCE_CONVERSATION_COL = "source_conversation_id"
_LEGACY_PAYLOAD_COL = "payload"
_CHANNEL_LEN = 32
_CONVERSATION_LEN = 128


def _table_columns(conn: sa.engine.Connection, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(conn).get_columns(table)}


def _coerce_str(value: object, *, max_len: int) -> str | None:
    """Best-effort string coercion; truncate to ``max_len``; ``None`` for non-strings.

    Mirrors the producer's typing: ``source_channel`` is plain ``str``
    and ``source_conversation_id`` is ``str | None``. A scalar number
    in old payloads gets stringified; a nested dict / list becomes
    ``None`` rather than a confusing opaque repr.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text[:max_len]


def upgrade() -> None:
    """Add the two source_* columns, drop the legacy ``payload`` column.

    Idempotent: a fresh DB that already has the columns (because the
    declarative ORM was updated before this migration ran) sees a
    no-op upgrade.
    """
    conn = op.get_bind()
    if _TABLE not in sa.inspect(conn).get_table_names():
        _log.info("0005: %s does not exist — skipping", _TABLE)
        return

    existing = _table_columns(conn, _TABLE)
    has_source_channel = _SOURCE_CHANNEL_COL in existing
    has_source_conversation = _SOURCE_CONVERSATION_COL in existing
    has_payload = _LEGACY_PAYLOAD_COL in existing

    if has_source_channel and has_source_conversation and not has_payload:
        _log.info("0005: %s already migrated — skipping", _TABLE)
        return

    # --- 1. Capture legacy payloads (if any) so we can copy them forward.
    legacy_rows: list[tuple[object, object | None]] = []
    if has_payload:
        legacy_rows = list(
            conn.execute(
                sa.text(
                    f"SELECT id, {_LEGACY_PAYLOAD_COL} FROM {_TABLE}"
                )
            ).fetchall()
        )

    # --- 2. Run DDL inside a FK-off window — same safeguard as 0014.
    op.execute("PRAGMA foreign_keys=OFF")
    try:
        if not has_source_channel:
            op.execute(
                sa.text(
                    f"ALTER TABLE {_TABLE} "
                    f"ADD COLUMN {_SOURCE_CHANNEL_COL} VARCHAR({_CHANNEL_LEN}) "
                    f"NOT NULL DEFAULT ''"
                )
            )
        if not has_source_conversation:
            op.execute(
                sa.text(
                    f"ALTER TABLE {_TABLE} "
                    f"ADD COLUMN {_SOURCE_CONVERSATION_COL} VARCHAR({_CONVERSATION_LEN})"
                )
            )
    finally:
        op.execute("PRAGMA foreign_keys=ON")

    # --- 3. Back-fill from legacy payload (if present).
    backfilled = 0
    for row in legacy_rows:
        row_id, raw_payload = row[0], row[1]
        if not isinstance(raw_payload, str) or not raw_payload.strip():
            continue
        try:
            parsed = json.loads(raw_payload)
        except (TypeError, ValueError):
            _log.warning(
                "0005: row id=%s had malformed payload — leaving source_* empty",
                row_id,
            )
            continue
        if not isinstance(parsed, dict):
            continue
        channel = _coerce_str(parsed.get("source_channel"), max_len=_CHANNEL_LEN)
        conversation = _coerce_str(
            parsed.get("source_conversation_id"), max_len=_CONVERSATION_LEN
        )
        if channel is None and conversation is None:
            continue
        conn.execute(
            sa.text(
                f"UPDATE {_TABLE} "
                f"SET {_SOURCE_CHANNEL_COL} = :channel, "
                f"    {_SOURCE_CONVERSATION_COL} = :conversation "
                f"WHERE id = :id"
            ),
            {
                "channel": channel or "",
                "conversation": conversation,
                "id": row_id,
            },
        )
        backfilled += 1

    # --- 4. Drop the legacy column.
    if has_payload:
        op.execute("PRAGMA foreign_keys=OFF")
        try:
            op.execute(sa.text(f"ALTER TABLE {_TABLE} DROP COLUMN {_LEGACY_PAYLOAD_COL}"))
        finally:
            op.execute("PRAGMA foreign_keys=ON")

    # --- 5. Catch any orphan FK left by the column swap.
    op.execute("PRAGMA foreign_key_check")

    if legacy_rows:
        _log.info(
            "0005: back-filled %d/%d rows from legacy payload into %s.%s / %s",
            backfilled,
            len(legacy_rows),
            _TABLE,
            _SOURCE_CHANNEL_COL,
            _SOURCE_CONVERSATION_COL,
        )
    else:
        _log.info("0005: no legacy payloads to back-fill")


def downgrade() -> None:
    """Recreate the legacy ``payload`` column and denormalize ``source_*`` back in.

    Best-effort downgrade: any ``source_channel`` / ``source_conversation_id``
    values are merged into a JSON dict so the next upgrade can recover
    them. Rows with both columns empty get a JSON ``"{}"`` literal so
    the column is uniformly non-NULL.
    """
    conn = op.get_bind()
    if _TABLE not in sa.inspect(conn).get_table_names():
        _log.info("0005: %s does not exist — skipping downgrade", _TABLE)
        return

    existing = _table_columns(conn, _TABLE)
    if _LEGACY_PAYLOAD_COL in existing:
        _log.info("0005: %s already has %s — skipping downgrade", _TABLE, _LEGACY_PAYLOAD_COL)
        return

    has_source_channel = _SOURCE_CHANNEL_COL in existing
    has_source_conversation = _SOURCE_CONVERSATION_COL in existing

    now = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"

    op.execute("PRAGMA foreign_keys=OFF")
    try:
        op.execute(
            sa.text(
                f"ALTER TABLE {_TABLE} "
                f"ADD COLUMN {_LEGACY_PAYLOAD_COL} TEXT NOT NULL DEFAULT '{{}}'"
            )
        )
    finally:
        op.execute("PRAGMA foreign_keys=ON")

    # Re-hydrate the payload column from the explicit source_* columns.
    if has_source_channel or has_source_conversation:
        rows = conn.execute(
            sa.text(
                f"SELECT id, {_SOURCE_CHANNEL_COL if has_source_channel else "''"}, "
                f"       {_SOURCE_CONVERSATION_COL if has_source_conversation else "NULL"} "
                f"FROM {_TABLE}"
            )
        ).fetchall()
        for row in rows:
            row_id = row[0]
            channel = (row[1] or "") if has_source_channel else ""
            conversation = row[2] if has_source_conversation else None
            payload = {}
            if channel:
                payload["source_channel"] = channel
            if conversation:
                payload["source_conversation_id"] = conversation
            conn.execute(
                sa.text(f"UPDATE {_TABLE} SET {_LEGACY_PAYLOAD_COL} = :p WHERE id = :id"),
                {"p": json.dumps(payload), "id": row_id, "ts": now},
            )

    op.execute("PRAGMA foreign_key_check")
