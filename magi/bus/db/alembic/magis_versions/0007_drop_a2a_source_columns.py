"""Drop ``source_channel`` / ``source_conversation_id`` / ``tool_call_id`` from A2A job tables.

Revision ID: 0007_drop_a2a_source_columns
Revises: 0006_split_a2a_request_payload_into_source_columns
Create Date: 2026-08-12 00:00:00.000000

The 0005 / 0006 revisions split ``source_channel`` /
``source_conversation_id`` out of an opaque ``payload`` JSON column
with the rationale that "target processing should not have to open a
JSON dict to recover the calling context." In practice no A2A receiver
ever read either column: ``AgentWorker._run`` already had everything it
needed from ``conversation_id`` plus the literal ``"a2a.request"`` /
``"a2a.notify"`` source string returned by ``claim_for_target``. The
split-out columns were therefore pure write-once storage with no
consumer.

``tool_call_id`` on ``a2a_request_jobs`` shares the same pathology: the
sender publishes with it, the receiver copies it verbatim into the
result, and nothing downstream ever reads it. The sender already
correlates its ``tool_call_id`` ↔ ``job_id`` mapping locally and polls
results by ``job_id``. The receiver-side echo is a no-op passthrough.

This revision drops all three columns. Two notes on data loss:

- ``source_conversation_id`` was a full duplicate of ``conversation_id``
  at the only write site, so nothing is lost — the conversation id
  already lives on the row.
- ``source_channel`` and ``tool_call_id`` carried unique values, but
  the original 0005 / 0006 docstrings already established that no
  producer / consumer pair ever referenced them. Dropping is the
  explicit intent.

``downgrade`` re-adds the columns empty so a rollback re-runs the
split DDL without resurrecting data. The split migrations' downgrade
path already documented that any ``source_*`` value recovery is
best-effort, so this matches that precedent.

DDL strategy
============

Both tables carry ``ON DELETE CASCADE`` foreign keys to
``magis_memberships``. We mirror the FK-off window used by 0005 /
0006 / 0014: ``PRAGMA foreign_keys=OFF`` … native ``ALTER TABLE ...
DROP COLUMN`` … ``PRAGMA foreign_keys=ON`` plus ``PRAGMA
foreign_key_check`` to catch any orphan. SQLite 3.35+ supports
``DROP COLUMN`` natively; the project enforces ``sqlite_version >=
3.36``. PostgreSQL accepts the same statements.
"""

from __future__ import annotations

from collections.abc import Sequence
import logging

import sqlalchemy as sa
from alembic import op

revision: str = "0007_drop_a2a_source_columns"
down_revision: str | Sequence[str] | None = (
    "0006_split_a2a_request_payload_into_source_columns"
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_log = logging.getLogger(__name__)

# (table, columns to drop on upgrade)
_DROP_PLAN: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "a2a_request_jobs",
        ("source_channel", "source_conversation_id", "tool_call_id"),
    ),
    (
        "a2a_notify_jobs",
        ("source_channel", "source_conversation_id"),
    ),
)
# Reverse: per-column definition used to re-add on downgrade.
# Mirrors the original 0005 / 0006 column declarations.
_COLUMN_DEF: dict[str, str] = {
    "source_channel": "VARCHAR(32) NOT NULL DEFAULT ''",
    "source_conversation_id": "VARCHAR(128)",
    "tool_call_id": "VARCHAR(128) NOT NULL DEFAULT ''",
}


def _table_columns(conn: sa.engine.Connection, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(conn).get_columns(table)}


def upgrade() -> None:
    """Drop dead A2A columns from both tables.

    Idempotent: a fresh DB that never had the columns (or already ran
    this migration) sees a no-op upgrade.
    """
    conn = op.get_bind()
    for table, cols in _DROP_PLAN:
        if table not in sa.inspect(conn).get_table_names():
            _log.info("0007: %s does not exist — skipping", table)
            continue

        existing = _table_columns(conn, table)
        to_drop = [col for col in cols if col in existing]
        if not to_drop:
            _log.info("0007: %s already migrated — skipping", table)
            continue

        op.execute("PRAGMA foreign_keys=OFF")
        try:
            for col in to_drop:
                op.execute(sa.text(f"ALTER TABLE {table} DROP COLUMN {col}"))
        finally:
            op.execute("PRAGMA foreign_keys=ON")

        op.execute("PRAGMA foreign_key_check")
        _log.info("0007: dropped %s from %s", ", ".join(to_drop), table)


def downgrade() -> None:
    """Re-add the columns empty so a rollback re-runs the split DDL.

    Best-effort: no data is recovered. ``source_channel`` and
    ``tool_call_id`` were the only pieces of unique data ever stored
    here, and the original 0005 / 0006 docstrings already declared that
    no consumer ever read either column, so dropping is by design.
    """
    conn = op.get_bind()
    for table, cols in _DROP_PLAN:
        if table not in sa.inspect(conn).get_table_names():
            _log.info("0007: %s does not exist — skipping downgrade", table)
            continue

        existing = _table_columns(conn, table)
        to_add = [col for col in cols if col not in existing]
        if not to_add:
            _log.info("0007: %s already has all columns — skipping", table)
            continue

        op.execute("PRAGMA foreign_keys=OFF")
        try:
            for col in to_add:
                op.execute(
                    sa.text(f"ALTER TABLE {table} ADD COLUMN {col} {_COLUMN_DEF[col]}")
                )
        finally:
            op.execute("PRAGMA foreign_keys=ON")

        op.execute("PRAGMA foreign_key_check")
        _log.info("0007: re-added %s (empty) to %s", ", ".join(to_add), table)
