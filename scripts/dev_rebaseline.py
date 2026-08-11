#!/usr/bin/env python3
"""Dev-mode DB rebaseline: dump → reset → restore.

A single-command helper for the "collapse migrations into
baseline" workflow. Backs up the live ``magi.db`` to a
JSON file, deletes the SQLite file (along with the FTS
shadow files and any leftover ``_alembic_tmp_*`` debris),
runs ``alembic upgrade head`` to rebuild a fresh schema,
and re-imports the dump into the new DB.

Intentionally NOT production-grade. Dev mode only — the
script assumes the operator is willing to lose:
  - any ``_alembic_tmp_*`` stale temp tables from a
    previous failed migration
  - the ``meta`` table (1 row, ``schema_version`` KV)
    that the new baseline doesn't recreate
  - the ``chat_messages_fts*`` shadow tables (rebuilt by
    the FTS5 triggers on ``chat_messages`` insert)

Usage::

    python scripts/dev_rebaseline.py            # dump + reset + restore in one go
    python scripts/dev_rebaseline.py dump       # just the JSON dump
    python scripts/dev_rebaseline.py reset      # delete DB file + alembic upgrade head
    python scripts/dev_rebaseline.py restore    # re-insert from the latest dump

The dump file lives at ``/tmp/magi-dump-<timestamp>.json``
by default — pass ``--dump <path>`` to override.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session


# -- paths ---------------------------------------------------------------

DEFAULT_DBS = [
    Path("~/.magi/MAGI_Citizens/eva-000/workspace/memories/magi.db").expanduser(),
]
# The "primary" DB — the dump targets this file (the others
# are smaller per-channel mirrors that share an overlapping
# table subset; in dev mode they're typically empty so we
# can rebuild them fresh without data loss). The reset
# step wipes both; the restore step only writes to this
# one.
PRIMARY_DB = DEFAULT_DBS[0]


# Tables whose existence doesn't make sense on the other side of
# the rebaseline — either internal (FTS5 shadow, alembic bookkeeping,
# leftover migration debris) or dropped (meta) by the new baseline.
# Skipped on both dump and restore.
SKIP_TABLES = frozenset({
    "alembic_version",
    "meta",
    "chat_messages_fts",
    "chat_messages_fts_config",
    "chat_messages_fts_data",
    "chat_messages_fts_docsize",
    "chat_messages_fts_idx",
})

# Insert order — parents first so child FKs resolve. Within a tier the
# order doesn't matter (no FKs across tables in the same tier).
INSERT_ORDER: list[str] = [
    # Tier 1: no business FKs
    "contacts",
    "magics",
    # Tier 2: depend on tier 1
    "magis",          # → magics.id
    "chat_conversations",  # → contacts.id
    "settings",       # KV — no FKs
    # Tier 3: depend on tier 2
    "action_items",   # → contacts.id
    "chat_messages",  # → chat_conversations.conversation_id
    "memory_entries", # → contacts.id
    "mcp_servers",    # no FKs
    "task_presets",   # no FKs (target of tasks.preset_id FK, but no FKs itself)
    # Tier 4: depend on tier 3
    "tasks",          # → contacts.id, chat_conversations.conversation_id, task_presets.id
    "token_usage",    # → contacts.id
    # Tier 5: depends on tier 4
    "task_runs",      # → tasks.id, chat_conversations.conversation_id
]


# -- helpers -------------------------------------------------------------

def _dump_one_db(path: Path) -> dict[str, list[dict]]:
    """Read every business table in ``path`` and return
    ``{table_name: [row_dicts]}``.

    Tables in :data:`SKIP_TABLES` are excluded. Datetimes
    are serialised as ISO strings (SQLAlchemy's default
    for ``datetime`` columns; the new baseline's INSERT
    accepts the same form).

    Per-row transforms applied before serialisation:
      - ``contacts`` rows with the legacy
        ``role='admin'`` value are rewritten to
        ``role='assigned', admin=True`` to match the
        collapsed ``0001_baseline`` schema (the role/admin
        split was folded into the baseline). The restored
        rows land in the post-baseline schema with the
        admin bit set, so the auth gate lets the operator
        sign back in.
    """
    if not path.exists():
        return {}
    eng = sa.create_engine(f"sqlite:///{path}")
    insp = sa.inspect(eng)
    out: dict[str, list[dict]] = {}
    with eng.connect() as conn:
        for table in insp.get_table_names():
            if table in SKIP_TABLES:
                continue
            if table.startswith("_alembic_tmp_"):
                continue
            # ``chat_messages`` is the biggest table — fetch all
            # rows via SQLAlchemy's reflection so we don't
            # hand-enumerate columns.
            rows = conn.execute(
                sa.text(f"SELECT * FROM {table}")
            ).mappings().all()
            rows = [dict(r) for r in rows]
            if table == "contacts":
                rows = [_transform_contact_role_split(r) for r in rows]
            out[table] = rows
    return out


def _transform_contact_role_split(row: dict) -> dict:
    """Apply the ``role='admin'`` → ``role='assigned', admin=True``
    data migration to a single contacts-table row.

    Pre-2024 contacts schema carried operator status in
    the ``role`` enum (``role='admin'`` meant "WebUI
    sign-in"). After the split those rows become
    ``role='assigned', admin=True``. We rewrite at dump
    time so the restored rows land in the new schema with
    the admin bit set — without this, an operator who
    ran ``dev_rebaseline.py all`` on a pre-split DB would
    end up with no admins (the auth gate filters on
    ``admin=True``).

    Idempotent: if the row already has ``role='assigned'``
    or ``admin=True``, it's left alone.
    """
    if row.get("role") == "admin":
        row["role"] = "assigned"
        row["admin"] = True
    return row


def cmd_dump(args: argparse.Namespace) -> int:
    """Read the primary DB (or the path passed via ``--db``)
    and write its tables to the JSON dump file.

    We only dump the primary DB. The per-channel mirrors
    (``telegram/magi.db`` etc.) typically overlap the
    primary's table set with no extra data — collapsing
    migrations into the baseline is a primary-DB operation;
    the mirrors get rebuilt fresh alongside it during
    ``reset``. Override ``--db`` to dump a non-default path.
    """
    db_path = Path(args.db[0]) if args.db else PRIMARY_DB
    dump_path = Path(args.dump)
    dump_path.parent.mkdir(parents=True, exist_ok=True)

    if not db_path.exists():
        print(f"ERROR: source DB not found: {db_path}", file=sys.stderr)
        return 1

    print(f"Dumping {db_path}:")
    per_db = _dump_one_db(db_path)
    payload: dict = {
        "dumped_at": datetime.now(timezone.utc).isoformat(),
        "source_db": str(db_path),
        "tables": per_db,
    }
    for table, rows in sorted(per_db.items()):
        print(f"  {table}: {len(rows)} rows")

    total_rows = sum(len(v) for v in per_db.values())
    dump_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"\nDumped {total_rows} rows across {len(per_db)} tables → {dump_path}")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    """Delete every default DB (or the path passed via ``--db``)
    plus their SQLite shadow files (``-wal``, ``-shm``, ``-journal``)
    and any leftover ``_alembic_tmp_*`` debris, then run
    ``alembic upgrade head`` to rebuild a fresh schema.

    ``alembic`` is invoked as a subprocess so we don't have to
    reload the cached engine singleton in-process. ``HOST_WORKSPACE_DIR``
    is set to the workspace root so ``<workspace>/MAGI_Citizens/<name>/memories/``
    resolves to the right state directory. ``PATH`` is inherited
    so the system ``.venv/bin/alembic`` is found.
    """
    db_paths = [Path(p) for p in args.db] if args.db else DEFAULT_DBS
    print("\n[reset] deleting DB files + shadow files:")
    for p in db_paths:
        for variant in (p, Path(str(p) + "-wal"), Path(str(p) + "-shm"),
                        Path(str(p) + "-journal")):
            if variant.exists():
                variant.unlink()
                print(f"  rm {variant}")

    # Find and run alembic against the project root. We point
    # ``HOST_WORKSPACE_DIR`` at the parent of the per-MAGI state dir
    # (the canonical layout is
    # ``<HOST>/MAGI_Citizens/<MAGI_NAME>/memories/magi.db``) so
    # ``resolve_state_dir()`` resolves to the right ``magi.db``
    # (the project's alembic env.py reads it).
    state_dir = db_paths[0].parent
    print(f"\n[reset] alembic upgrade head (state_dir={state_dir})")
    env = os.environ.copy()
    # state_dir is ``<HOST>/MAGI_Citizens/<name>/memories``; the
    # host root is three levels up.
    host_root = state_dir.parent.parent.parent
    env["HOST_WORKSPACE_DIR"] = str(host_root)
    env["MAGI_NAME"] = state_dir.parent.name
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd="/root/GitHub/MAGI",
        env=env,
        capture_output=True,
        text=True,
    )
    # Print the tail of alembic's log so we can confirm
    # the baseline ran without dropping the operator into
    # a wall of debug.
    print(result.stdout[-2000:] if result.stdout else "")
    if result.returncode != 0:
        print(result.stderr[-2000:])
        return result.returncode
    return 0


def _restore_table(session: Session, table: str, rows: list[dict],
                   target_columns: list[str]) -> tuple[int, str | None]:
    """INSERT every row into ``table`` using only the columns
    present in the **target** schema.

    The dump captures the source's columns verbatim — but
    after the rebaseline the target may have fewer columns
    (e.g. a legacy ``employee_id`` was renamed to ``uid``
    between source and target) or reordered. Building the
    INSERT from ``target_columns`` instead of ``row.keys()``
    drops the legacy columns naturally so a D.23-era
    action_items dump with ``completed_by_employee_id``
    cleanly lands in the target's ``completed_by_uid`` schema.

    Returns ``(inserted_count, error_message)``. On error,
    the caller should roll back the session.
    """
    inserted = 0
    for row in rows:
        # Project to the target's column set, skipping any
        # column the source had but the target doesn't.
        params = {col: row[col] for col in target_columns if col in row}
        session.execute(
            sa.text(
                f"INSERT OR REPLACE INTO {table} "
                f"({', '.join(target_columns)}) "
                f"VALUES ({', '.join(':' + c for c in target_columns)})"
            ),
            params,
        )
        inserted += 1
    return inserted, None


def cmd_restore(args: argparse.Namespace) -> int:
    """Re-insert rows from the JSON dump into the live DB.

    Inserts in :data:`INSERT_ORDER` order so every FK resolves.
    Tables not present in the dump are skipped (a fresh DB
    with no rows is a valid restore point).
    """
    dump_path = Path(args.dump)
    if not dump_path.exists():
        print(f"ERROR: dump file not found: {dump_path}", file=sys.stderr)
        return 1
    payload = json.loads(dump_path.read_text())
    tables: dict[str, list[dict]] = payload.get("tables", {})

    db_path = Path(args.db[0]) if args.db else PRIMARY_DB
    if not db_path.exists():
        print(f"ERROR: target DB not found: {db_path}. Run reset first.", file=sys.stderr)
        return 1

    eng = sa.create_engine(f"sqlite:///{db_path}")
    insp = sa.inspect(eng)
    existing = set(insp.get_table_names())
    inserted: dict[str, int] = {}
    skipped: dict[str, str] = {}

    # Build a ``{table: [target_column_names]}`` map once.
    # The restore inserts using ONLY the target's columns,
    # so any legacy column in the source dump (e.g.
    # ``completed_by_employee_id`` pre-D.23) is silently
    # dropped rather than triggering a 1x1 column-count
    # INSERT error.
    target_cols: dict[str, list[str]] = {
        t: [c["name"] for c in insp.get_columns(t)] for t in existing
    }

    with Session(eng) as session:
        # Walk the explicit INSERT_ORDER; any table in the dump
        # not in this list (e.g. a future ad-hoc table) lands
        # at the end with no FK guarantee.
        seen = set()
        for table in INSERT_ORDER:
            seen.add(table)
            if table not in existing:
                skipped[table] = "table missing in target DB"
                continue
            rows = tables.get(table, [])
            if not rows:
                continue
            try:
                n, _ = _restore_table(session, table, rows, target_cols[table])
                session.commit()
                inserted[table] = n
            except Exception as exc:
                session.rollback()
                skipped[table] = f"{type(exc).__name__}: {str(exc)[:200]}"
        # Catch-all for any tables in the dump that aren't
        # in our INSERT_ORDER list — log but don't fail.
        for table in tables:
            if table in seen:
                continue
            rows = tables[table]
            if table not in existing:
                skipped[table] = "table missing in target DB"
                continue
            try:
                n, _ = _restore_table(session, table, rows, target_cols[table])
                session.commit()
                inserted[table] = n
            except Exception as exc:
                session.rollback()
                skipped[table] = f"{type(exc).__name__}: {str(exc)[:200]}"

    print("\n[restore] inserted:")
    for t, n in inserted.items():
        print(f"  {t}: {n} rows")
    if skipped:
        print("\n[restore] skipped:")
        for t, why in skipped.items():
            print(f"  {t}: {why}")
    total = sum(inserted.values())
    print(f"\n[restore] {total} rows restored from {dump_path}")
    return 0


# -- entry ---------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = parser.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--db", action="append", default=None,
        help="Override primary DB path (default: workspace/ADAM/memories/magi.db). "
             "For ``reset`` only, this is repeated to wipe extra DBs too.",
    )
    common.add_argument(
        "--dump", default=None,
        help="Dump file path (default: /tmp/magi-dump-<timestamp>.json)",
    )

    p_dump = sub.add_parser("dump", parents=[common], help="Read DBs → JSON")
    p_reset = sub.add_parser("reset", parents=[common], help="rm DB → alembic upgrade head")
    p_restore = sub.add_parser("restore", parents=[common], help="JSON → DB")
    p_all = sub.add_parser("all", parents=[common], help="dump + reset + restore")

    args = parser.parse_args()
    if args.dump is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        args.dump = f"/tmp/magi-dump-{ts}.json"

    if args.cmd == "dump":
        return cmd_dump(args)
    if args.cmd == "reset":
        return cmd_reset(args)
    if args.cmd == "restore":
        return cmd_restore(args)
    if args.cmd == "all":
        # ``all`` reuses the dump path produced by the dump
        # step so a re-run with the same args picks up the
        # same file. Print the path so the operator can
        # keep it for later re-runs.
        rc = cmd_dump(args)
        if rc != 0:
            return rc
        print(f"\nDump file for restore: {args.dump}\n")
        rc = cmd_reset(args)
        if rc != 0:
            return rc
        return cmd_restore(args)
    parser.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())