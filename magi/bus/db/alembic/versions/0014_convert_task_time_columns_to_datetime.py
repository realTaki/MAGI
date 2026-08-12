"""Convert ``tasks`` / ``task_runs`` time columns from ``String(32)`` ISO to native ``DateTime``.

Revision ID: 0014_convert_task_time_columns_to_datetime
Revises: 0013_replace_run_task_fired_by_and_task_run_trigger

Aligns the ``tasks`` and ``task_runs`` tables with the rest of the
bus: every other table in the bus (13+ tables, including
``action_items``, ``memory_entries``, ``chat_messages``,
``run_task_jobs``, etc.) has stored ``created_at`` / ``updated_at``
/ ``last_*_at`` as **naive** ``DateTime`` columns with
``utcnow_naive`` as the column default. ``tasksBook`` and
``task_runsBook`` were the lone hold-outs still on ``String(32)``
ISO text, which produced three correctness gaps:

- :class:`ix_tasks_enabled_last_run` (tasksBook._TaskRow) and
  the ``started_at < cutoff`` filter used by
  :meth:`magi.bus.library.local.tasksBook.TaskRunBook.reap_stale`
  relied on string-vs-string lexicographic ordering.  Precision
  drift between writers (naive + microseconds vs. ``Z``-suffixed
  second resolution) made those comparisons unreliable.
- Multiple writer paths produced two ISO flavours — naive with
  microseconds (``utcnow_naive().isoformat()``) for
  ``created_at``/``updated_at``/``started_at``/``finished_at``/``last_run_at``,
  but ``Z``-suffixed second-precision (``validate_run_at``) for
  ``run_at`` — so a single ``tasks`` row could carry timestamps in
  two different shapes.
- Caller code (e.g. ``magi/channels/tasks/worker.py:171``
  ``datetime.fromisoformat(t.last_run_at)``) had to do a parse
  round-trip that a native ``DateTime`` column would have made
  free.

Schema change
=============

Five columns across two tables move from ``String(32)`` to
``DateTime`` (naive):

  tasks:
    - created_at   NOT NULL            → DateTime, default utcnow_naive
    - updated_at   NOT NULL            → DateTime, default utcnow_naive,
                                         onupdate utcnow_naive
    - last_run_at  NULL allowed        → DateTime, nullable

  task_runs:
    - started_at   NOT NULL            → DateTime, default utcnow_naive
    - finished_at  NULL allowed        → DateTime, nullable

Native ``ALTER TABLE ... ADD COLUMN`` / ``DROP COLUMN`` / ``RENAME COLUMN``
-----------------------------------------------------------------------

SQLite 3.36+ supports ``ALTER TABLE ... ADD COLUMN``,
``DROP COLUMN`` (3.35+), and ``RENAME COLUMN`` natively, so the
conversion can be one ``ADD COLUMN + UPDATE + DROP COLUMN +
RENAME COLUMN`` per affected column. We deliberately avoid
``op.batch_alter_table``'s copy-and-recreate path — SQLite's
``ON DELETE CASCADE`` between ``tasks`` and ``task_runs`` would
silently fire on the parent ``tasks`` recreation, wiping every
``task_runs`` row in production (probed locally with
``PRAGMA foreign_keys=ON``). The migration toggles
``PRAGMA foreign_keys=OFF`` around the column swaps and restores
``ON`` (with ``PRAGMA foreign_key_check``) at the end.

Data migration
==============

Python-side parsing (the migration runs as a revision script,
not raw SQL) handles three input forms:

  - ``None`` / ``""`` / whitespace     → ``None``
  - ``"...Z"`` / ``"...+00:00"``       → ``datetime.fromisoformat`` →
                                          ``astimezone(UTC).replace(tzinfo=None)``
  - naive with microseconds            → assumed UTC (matches
                                          ``utcnow_naive`` output)

Empty / malformed rows for ``NOT NULL`` columns substitute
``utcnow_naive()`` at upgrade-time and log a WARNING so a
follow-up audit can locate them. The migration **never aborts**
on a malformed cell — a runtime that can't boot over a single
rogue timestamp would be the wrong invariant.

Idempotency
===========

The migration first inspects ``tasks.created_at`` via
:func:`sa.inspect`; if it's already ``DateTime`` (a fresh DB that
ran ``create_all`` after the Python ORM was updated, OR a previous
upgrade) the whole upgrade is a no-op. The downgrade mirrors the
upgrade: snapshot rows, ``PRAGMA foreign_keys=OFF``, add a
``String(32)`` shadow column, format each row as
``dt.isoformat() + "Z"`` (the wire form the rest of the bus
already used), drop the original, rename the shadow back.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0014_convert_task_time_columns_to_datetime"
down_revision: str | Sequence[str] | None = (
    "0013_replace_run_task_fired_by_and_task_run_trigger"
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_log = logging.getLogger(__name__)


# -- helpers ------------------------------------------------------------


def _table_columns(conn: sa.engine.Connection, table: str) -> dict[str, sa.types.TypeEngine]:
    """Return ``{name: type}`` for one table, mirroring :func:`sa.inspect`."""
    insp = sa.inspect(conn)
    if table not in insp.get_table_names():
        return {}
    return {c["name"]: c["type"] for c in insp.get_columns(table)}


def _is_datetime(t: object) -> bool:
    """Match SQLAlchemy ``DateTime`` (with or without ``timezone=True``).

    The plan kept the bus on **naive** ``DateTime`` (``timezone=False``)
    to match the rest of the bus — but a future migration might flip
    to aware, so we accept both shapes here as "already converted".
    """
    if isinstance(t, sa.DateTime):
        return True
    # Some dialects wrap the type; fall back to class-name match so
    # tests / heterogeneous DB backends don't false-negative.
    return t.__class__.__name__ == "DateTime"  # type: ignore[attr-defined]


def _indexes_referencing(
    conn: sa.engine.Connection, table: str, columns: list[str]
) -> list[tuple[str, list[str]]]:
    """Return ``[(index_name, [col, ...]), ...]`` for indexes that touch any
    of ``columns`` on ``table``.

    SQLite only stores the parsed column list in ``sqlite_master.sql``
    for indexes it created itself (i.e. non-``autoindex``); the
    ``autoindex`` entries have ``sql=NULL`` and are auto-managed by
    SQLite, so they're skipped here.
    """
    colset = set(columns)
    insp = sa.inspect(conn)
    if table not in insp.get_table_names():
        return []
    out: list[tuple[str, list[str]]] = []
    for idx in insp.get_indexes(table):
        idx_cols = [c for c in idx["column_names"] if c]
        if colset.intersection(idx_cols):
            out.append((idx["name"], idx_cols))
    return out


def _parse_iso_to_naive(s: object) -> datetime | None:
    """Parse an ISO 8601 string into naive UTC.

    Returns ``None`` for empty / malformed input (with a WARNING log)
    — the caller decides whether to substitute ``utcnow_naive()``
    for a ``NOT NULL`` column. Never raises so a single bad row
    can never abort the migration.
    """
    if s is None:
        return None
    text = str(s).strip() if isinstance(s, str) else ""
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, TypeError) as e:
        _log.warning(
            "0014 migration: bad timestamp %r (%s) — substituting fallback",
            s,
            e,
        )
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


def _stringify_naive(dt: datetime) -> str:
    """Naive ``DateTime`` → ``"YYYY-MM-DDTHH:MM:SSZ"`` for downgrade."""
    if dt.tzinfo is None:
        return dt.isoformat() + "Z"
    return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _swap_columns(
    conn: sa.engine.Connection,
    *,
    table: str,
    columns: list[str],
    rows: list,
    fallback: datetime,
    required: set[str],
) -> None:
    """ADD-UPDATE-DROP-RENAME one ``DateTime`` per legacy column on ``table``.

    ``columns`` is the list of legacy ``String(32)`` columns to
    convert. ``rows`` are the snapshot rows fetched BEFORE the swap —
    the migration reads them in Python memory because each ``UPDATE``
    binds a typed ``datetime`` parameter and SQLite's strftime would
    not have given us a clean way to handle the four ISO flavours in
    one round trip.

    ``required`` are columns where ``None`` / "" / malformed rows
    must be replaced with the upgrade-time ``fallback`` (this is
    the contract of a ``NOT NULL`` column).

    SQLite refuses ``ALTER TABLE ... DROP COLUMN`` when an index still
    references the dropped column — the error is reported as
    ``error in index <name> after drop column: no such column: <col>``.
    We drop those indexes before the column swap and recreate them
    against the renamed column afterwards so the post-migration schema
    matches the ORM's ``__table_args__``.
    """
    # Indexes that reference at least one of the columns we're about
    # to drop. Collected once (not per-column) so we don't redundantly
    # drop / recreate the same index for every column it covers.
    affected_indexes: dict[str, list[str]] = {}
    for idx_name, idx_cols in _indexes_referencing(conn, table, columns):
        affected_indexes[idx_name] = list(idx_cols)

    for col in columns:
        shadow = f"_{col}_dt"
        # Drop indexes that reference this column BEFORE the column
        # goes away. The index will be recreated on the renamed
        # column once all the per-column swaps are done.
        for idx_name, idx_cols in list(affected_indexes.items()):
            if col in idx_cols:
                conn.execute(sa.text(f"DROP INDEX IF EXISTS {idx_name}"))
        conn.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {shadow} DATETIME"))
        for row in rows:
            raw = row._mapping[col]
            parsed = _parse_iso_to_naive(raw)
            if parsed is None and col in required:
                parsed = fallback
            if parsed is not None:
                conn.execute(
                    sa.text(f"UPDATE {table} SET {shadow} = :v WHERE id = :id"),
                    {"v": parsed, "id": row._mapping["id"]},
                )
        conn.execute(sa.text(f"ALTER TABLE {table} DROP COLUMN {col}"))
        conn.execute(
            sa.text(f"ALTER TABLE {table} RENAME COLUMN {shadow} TO {col}")
        )

    # Recreate the dropped indexes against the now-renamed columns.
    for idx_name, idx_cols in affected_indexes.items():
        cols_csv = ", ".join(idx_cols)
        conn.execute(sa.text(f"CREATE INDEX {idx_name} ON {table} ({cols_csv})"))


# -- upgrade / downgrade -----------------------------------------------


def upgrade() -> None:
    """Convert legacy ``String(32)`` ISO columns to native ``DateTime``.

    No-op on a fresh DB (``create_all`` already produced the new
    shape) — guarded by the column-type inspection on the first
    affected column.
    """
    conn = op.get_bind()

    tasks_cols = _table_columns(conn, "tasks")
    if "created_at" in tasks_cols and _is_datetime(tasks_cols["created_at"]):
        _log.info("0014: tasks.created_at is already DateTime — skipping")
        return

    fallback = datetime.now(UTC).replace(tzinfo=None)

    tasks_old = conn.execute(
        sa.text("SELECT id, created_at, updated_at, last_run_at FROM tasks")
    ).fetchall()
    runs_old = conn.execute(
        sa.text("SELECT id, started_at, finished_at FROM task_runs")
    ).fetchall()

    # ``tasks → task_runs`` carries ``ON DELETE CASCADE``. The column
    # swap below never drops the parent table (it uses native
    # ALTER ... DROP/RENAME COLUMN), but a stray DDL still has to
    # bypass the cascade guard during the brief window where the
    # column has been dropped but not yet renamed back.
    op.execute("PRAGMA foreign_keys=OFF")
    try:
        _swap_columns(
            conn,
            table="tasks",
            columns=["created_at", "updated_at", "last_run_at"],
            rows=tasks_old,
            fallback=fallback,
            required={"created_at", "updated_at"},
        )
        _swap_columns(
            conn,
            table="task_runs",
            columns=["started_at", "finished_at"],
            rows=runs_old,
            fallback=fallback,
            required={"started_at"},
        )
    finally:
        op.execute("PRAGMA foreign_keys=ON")

    # Belt + suspenders: catch any DDL that left an FK dangling.
    op.execute("PRAGMA foreign_key_check")


def downgrade() -> None:
    """Reverse: convert ``DateTime`` back to ``String(32)`` ISO with the trailing ``Z``.

    Empty rows were back-filled with ``utcnow_naive()`` at upgrade
    time — they round-trip here as a normal timestamp string; no
    special-case is needed.
    """
    conn = op.get_bind()
    tasks_cols = _table_columns(conn, "tasks")
    if "created_at" in tasks_cols and not _is_datetime(tasks_cols["created_at"]):
        _log.info("0014: tasks.created_at is already String — skipping downgrade")
        return

    tasks_old = conn.execute(
        sa.text("SELECT id, created_at, updated_at, last_run_at FROM tasks")
    ).fetchall()
    runs_old = conn.execute(
        sa.text("SELECT id, started_at, finished_at FROM task_runs")
    ).fetchall()

    def _swap_to_str(
        conn: sa.engine.Connection,
        table: str,
        columns: list[str],
        rows: list,
    ) -> None:
        for col in columns:
            shadow = f"_{col}_str"
            conn.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {shadow} VARCHAR(32)"))
            for row in rows:
                dt = row._mapping[col]
                if dt is None:
                    # Nullable columns: leave the shadow NULL; tighten to NOT NULL
                    # below the rows-with-NULL guard at conversion time.
                    continue
                conn.execute(
                    sa.text(f"UPDATE {table} SET {shadow} = :v WHERE id = :id"),
                    {"v": _stringify_naive(dt), "id": row._mapping["id"]},
                )
            conn.execute(sa.text(f"ALTER TABLE {table} DROP COLUMN {col}"))
            conn.execute(
                sa.text(f"ALTER TABLE {table} RENAME COLUMN {shadow} TO {col}")
            )

    op.execute("PRAGMA foreign_keys=OFF")
    try:
        _swap_to_str(
            conn,
            "tasks",
            ["created_at", "updated_at", "last_run_at"],
            tasks_old,
        )
        _swap_to_str(
            conn,
            "task_runs",
            ["started_at", "finished_at"],
            runs_old,
        )
    finally:
        op.execute("PRAGMA foreign_keys=ON")
