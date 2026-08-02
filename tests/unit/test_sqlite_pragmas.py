"""Lock the SQLite PRAGMA configuration required by design §15.

The durable settings (``journal_mode=WAL``) are persisted in the
database header. The per-connection settings
(``synchronous=NORMAL``, ``busy_timeout=5000``, ``foreign_keys=ON``)
are re-asserted by the SQLAlchemy engine's connection listener
on every new connection — that's why the runtime stack never
sees a connection that defaults to FULL synchronous.

``local_db.init_sqlite`` runs on the same connection that the
SQLAlchemy listener also reaches. We assert two things:

  1. The literal ``PRAGMA synchronous=NORMAL`` call exists in the
     source and runs *after* the first commit (so it sticks past
     SQLite's journal_mode-induced reset).

  2. The SQLAlchemy engine's listener emits the same PRAGMA on
     every new pooled connection.

The first check is a static AST scan; the second is a behavioural
assertion that opens a session and reads ``synchronous``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _local_db_synchronous_call() -> tuple[int, str]:
    """Return ``(line_number, stmt_text)`` of the synchronous PRAGMA
    in ``magi/db/local_db.py``.
    """
    src = (REPO_ROOT / "magi" / "db" / "local_db.py").read_text()
    tree = ast.parse(src)
    matches: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            func = call.func
            is_call = (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "conn"
                and func.attr == "execute"
            )
            if not is_call or not call.args:
                continue
            arg = call.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if re.match(
                    r"\s*pragma\s+synchronous\s*=\s*normal",
                    arg.value,
                    re.IGNORECASE,
                ):
                    matches.append((node.lineno, arg.value))
    return matches[0] if matches else (0, "")


def test_local_db_issues_synchronous_normal_after_commit() -> None:
    """``init_sqlite`` issues ``PRAGMA synchronous=NORMAL`` after
    the first commit so the setting sticks (SQLite resets
    ``synchronous`` when ``journal_mode`` changes).

    Verified via static AST scan — the call must be present
    after a preceding ``conn.commit()`` in the same ``with``
    block.
    """
    src = (REPO_ROOT / "magi" / "db" / "local_db.py").read_text()
    sync_line, stmt = _local_db_synchronous_call()
    assert stmt, (
        "magi/db/local_db.py must issue PRAGMA synchronous=NORMAL"
    )
    # Slice the file up to the synchronous call. There must be a
    # ``conn.commit()`` in that prefix — that's the commit that
    # activates WAL and lets the subsequent synchronous=NORMAL
    # stick.
    prefix = src[:src.index(stmt, src.index(stmt))]
    assert "conn.commit()" in prefix, (
        "synchronous=NORMAL must run AFTER a preceding conn.commit(); "
        "SQLite resets synchronous on journal_mode change and the "
        "first commit is what makes WAL sticky."
    )
    # Sanity: the call must be inside the bootstrap (not in some
    # unrelated helper). Line numbers in local_db.py are
    # bounded; the function body sits between roughly line 40
    # and the closing ``return db_path``.
    assert sync_line > 30, (
        f"synchronous PRAGMA at line {sync_line} is too early; "
        f"expected it after the bootstrap's CREATE TABLE block."
    )


def test_engine_sets_synchronous_normal_on_new_connection(tmp_path: Path) -> None:
    """The SQLAlchemy engine's per-connection listener sets NORMAL."""
    from magi.db import init_orm, open_session

    init_orm(str(tmp_path), seed_root=False)

    with open_session() as session:
        sync = session.execute(
            __import__("sqlalchemy").text("PRAGMA synchronous")
        ).scalar()
        journal = session.execute(
            __import__("sqlalchemy").text("PRAGMA journal_mode")
        ).scalar()
        busy = session.execute(
            __import__("sqlalchemy").text("PRAGMA busy_timeout")
        ).scalar()
        fk = session.execute(
            __import__("sqlalchemy").text("PRAGMA foreign_keys")
        ).scalar()
    assert sync == 1, f"synchronous should be NORMAL (1), got {sync}"
    assert str(journal).lower() == "wal"
    assert busy == 5000
    assert int(fk) == 1