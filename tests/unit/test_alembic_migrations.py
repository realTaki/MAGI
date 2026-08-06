"""Regression tests for the alembic migration chain.

These tests guard the dev-mode rebase pattern: ``CANONICAL_HEAD`` must
match the real terminal revision, and a DB whose ``alembic_version``
row sits at a revision Alembic no longer ships must have its bookkeeping
re-stamped by ``upgrade_head`` rather than crashing the boot.

The runtime ships a **single** alembic revision
(``0001_initial_schema``); every schema feature lives in that one
baseline. In a dev environment that means existing local databases
whose ``alembic_version.version_num`` points at a now-deleted
revision (e.g. ``0005_agent_bus``) still boot cleanly:
``_rebase_to_canonical_head`` notices the unknown revision, blanks
the row, and re-stamps to the canonical head. A fresh DB just runs
the single baseline migration from scratch.

Without these tests a future refactor that splits the lone migration
into a chain (or removes the rebase guard) would silently break the
runtime: every ORM operation that touches a column defined elsewhere
would crash.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def test_canonical_head_equals_real_head() -> None:
    """``alembic_runner.CANONICAL_HEAD`` must be the terminal head.

    Static guard. If a future revision is added but the constant is not
    bumped, ``upgrade_head`` will silently leave the new migration
    un-applied on every boot.
    """
    from magi.bus.db.alembic_runner import CANONICAL_HEAD, _ALEMBIC_SCRIPT_LOCATION
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option(
        "script_location",
        str(_ALEMBIC_SCRIPT_LOCATION),
    )
    heads = set(ScriptDirectory.from_config(config).get_heads())
    assert CANONICAL_HEAD in heads, (
        f"CANONICAL_HEAD={CANONICAL_HEAD!r} is not a terminal revision; "
        f"real heads are {sorted(heads)}. Update the constant."
    )


def _raw_alembic_version(db_path: Path) -> str | None:
    """Read ``alembic_version.version_num`` via a raw sqlite3 connection.

    Bypasses the SQLAlchemy engine so a test can probe the migration
    state without triggering ``init_orm`` to apply outstanding
    migrations.
    """
    raw = sqlite3.connect(str(db_path))
    try:
        row = raw.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row is not None else None
    finally:
        raw.close()


def _columns(db_path: Path, table: str) -> set[str]:
    raw = sqlite3.connect(str(db_path))
    try:
        return {row[1] for row in raw.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()}
    finally:
        raw.close()


def test_fresh_db_stamps_at_canonical_head(monkeypatch, tmp_path: Path) -> None:
    """A clean init_orm must end at CANONICAL_HEAD.

    The dev-mode rebase collapsed every schema change into a single
    baseline migration. A fresh workspace boots, runs that one migration,
    and ``alembic_version`` reads the canonical head.
    """
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(tmp_path))

    # Force the engine cache to rebuild against the tmp_path DB.
    import magi.bus.db.engine as engine_mod
    engine_mod._engine = engine_mod._SessionLocal = None

    from magi.bus.db import init_orm
    from magi.bus.db.alembic_runner import CANONICAL_HEAD
    from magi.startup.paths import resolve_state_dir as launcher_state_dir

    init_orm(seed_root=False)

    db_path = Path(launcher_state_dir()) / "magi.db"
    assert _raw_alembic_version(db_path) == CANONICAL_HEAD, (
        f"fresh DB did not land at CANONICAL_HEAD={CANONICAL_HEAD!r}"
    )


def test_legacy_db_unknown_revision_is_rebased(monkeypatch, tmp_path: Path) -> None:
    """A DB whose ``alembic_version`` points at a deleted revision must
    be re-stamped to the canonical head, not crash the boot.

    Simulates the post-rebase dev scenario: a developer's local
    ``magi.db`` still carries ``alembic_version.version_num =
    '0005_agent_bus'`` from before the 2026.08 rebase folded every
    schema change into ``0001_initial_schema``. The script directory
    no longer knows that revision; without the rebase guard, Alembic
    would raise ``Can't locate revision`` on every boot. With the
    guard, ``upgrade_head`` notices the unknown revision, blanks the
    row, and re-stamps.

    The schema itself is already correct — every column the deleted
    revisions added is part of the new baseline. The only thing
    changing is the bookkeeping row.
    """
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(tmp_path))

    import magi.bus.db.engine as engine_mod
    engine_mod._engine = engine_mod._SessionLocal = None

    from magi.bus.db import init_orm
    from magi.bus.db.alembic_runner import CANONICAL_HEAD
    from magi.startup.paths import resolve_state_dir as launcher_state_dir

    state_path = Path(launcher_state_dir())
    db_path = state_path / "magi.db"

    # Seed a fake ``alembic_version`` row pointing at a revision the
    # script directory no longer ships. The schema is a fresh DB so the
    # shape already matches the post-rebaseline state — we just want to
    # prove the rebase retargets the row instead of crashing.
    import sqlalchemy as sa
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.touch()
    seed_engine = sa.create_engine(f"sqlite:///{db_path}")
    with seed_engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        ))
        conn.execute(sa.text(
            "INSERT INTO alembic_version (version_num) VALUES ('0005_agent_bus')"
        ))
    seed_engine.dispose()

    # Boot must succeed and leave alembic_version at the canonical head.
    init_orm(seed_root=False)

    assert _raw_alembic_version(db_path) == CANONICAL_HEAD, (
        f"unknown revision was not re-stamped; expected {CANONICAL_HEAD!r}"
    )


@pytest.mark.parametrize("table,expected_columns", [
    # Every schema feature the actor runtime + idempotency + ordinal +
    # metadata migrations folded into the single baseline. If any of
    # these regresses, the runtime will crash at first ORM query
    # against the column.
    ("agent_inbox", {
        "conversation_id", "correlation_id", "causation_id",
        "source_type", "external_event_id",
    }),
    ("run_inputs", {
        "received_seq", "context_seq", "status", "source_event_id",
    }),
    ("llm_attempts", {
        "inbox_event_id", "provider", "model", "last_stream_seq",
    }),
    ("chat_messages", {
        "content_blocks", "run_id", "llm_attempt_id",
    }),
    ("a2a_invocations", {
        "tool_call_id", "request_event_id", "reply_to",
        "expect_reply", "deadline_at", "idempotency_key",
    }),
    ("tool_jobs", {"idempotency_key"}),
    ("delivery_outbox", {"event_id", "idempotency_key"}),
    ("tool_calls", {"ordinal", "ordered_at"}),
    ("agent_runs", {
        "expected_tool_call_ids", "expected_a2a_invocation_ids",
        "iteration_count", "token_usage", "deadline_at",
    }),
])
def test_baseline_includes_all_actor_runtime_columns(
    table: str, expected_columns: set[str], monkeypatch, tmp_path: Path,
) -> None:
    """The collapsed baseline must include every column the actor
    runtime expects. Parametrised so a missing column fails with a
    named assertion rather than a generic ``assert set.issubset`` dump.
    """
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(tmp_path))

    import magi.bus.db.engine as engine_mod
    engine_mod._engine = engine_mod._SessionLocal = None

    from magi.bus.db import init_orm
    from magi.startup.paths import resolve_state_dir as launcher_state_dir

    init_orm(seed_root=False)

    db_path = Path(launcher_state_dir()) / "magi.db"
    actual = _columns(db_path, table)
    missing = expected_columns - actual
    assert not missing, f"{table!r} is missing columns: {sorted(missing)}"
