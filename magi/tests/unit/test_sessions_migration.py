"""Tests for :func:`magi.agent.memory.session.migrate_from_json` (D.18).

Pins the boot-time importer that walks the legacy
``<workspace>/memories/sessions/<tgid>/<sid>.json`` tree
and copies each file into the SQLite ``chat_sessions`` +
``chat_messages`` tables.

Three contracts:

  1. **Happy path** — every JSON file becomes a row, with
     active + archive messages preserved (``archived`` flag
     reflects the legacy ``messages`` vs ``archive`` lists).
  2. **Idempotent re-run** — a second call to
     ``migrate_from_json`` after the first leaves the DB
     alone (``INSERT OR IGNORE`` on the
     ``(session_id, message_id)`` unique constraint).
  3. **Corrupt files are not deleted** — a malformed JSON
     file gets logged + skipped, NOT removed. The
     ``corrupt`` counter increments; the operator can
     hand-inspect / fix / ``rm`` it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ────────────────────────────────────────────────────────────────── #
# fixtures
# ────────────────────────────────────────────────────────────────── #


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    """Fresh state dir + ORM engine per test."""
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))

    import magi.agent.db.engine as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None

    from magi.agent.db import init_sqlite
    from magi.agent.db import init_orm
    init_sqlite(str(state))
    init_orm(str(state))

    # ``workspace_root`` defaults to ``state_dir.parent`` — i.e.
    # ``tmp_path`` for this fixture. The legacy JSON tree
    # lives at ``<workspace>/memories/sessions/<tgid>/...``.
    return state, tmp_path


def _write_legacy_session(
    workspace: Path,
    tgid: str,
    session_id: str,
    *,
    messages: list[dict] | None = None,
    archive: list[dict] | None = None,
    title: str | None = None,
    active_tail_count: int = 20,
    last_compaction_at: str | None = None,
) -> Path:
    """Write a pre-D.18 JSON session file at the canonical
    layout. Returns the file path so tests can ``.unlink()``
    or assert on it.
    """
    sessions_root = workspace / "memories" / "sessions" / tgid
    sessions_root.mkdir(parents=True, exist_ok=True)
    path = sessions_root / f"{session_id}.json"
    payload = {
        "schema_version": 1,
        "session_id": session_id,
        "tgid": tgid,
        "uid": 42,
        "channel": "webui",
        "created_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-07-02T00:00:00Z",
        "title": title,
        "messages": messages or [],
        "archive": archive or [],
        "active_tail_count": active_tail_count,
        "last_compaction_at": last_compaction_at,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


# ────────────────────────────────────────────────────────────────── #
# happy path
# ────────────────────────────────────────────────────────────────── #


def test_migrate_imports_active_messages(fresh_db):
    """Active messages land in ``chat_messages`` with
    ``archived=0`` and the FTS5 trigger picks them up."""
    from magi.agent.memory.session import migrate_from_json
    from magi.agent.db import (
        ChatMessage,
        ChatSession,
        open_session,
    )

    state, workspace = fresh_db
    _write_legacy_session(
        workspace, "9001", "01ABC",
        messages=[
            {"role": "user", "text": "hello",
             "ts": "2026-07-01T00:00:00Z", "message_id": "m1"},
            {"role": "assistant", "text": "hi",
             "ts": "2026-07-01T00:00:01Z", "message_id": "m2"},
        ],
    )

    stats = migrate_from_json(workspace)
    assert stats == {"imported": 1, "skipped": 0, "corrupt": 0}

    with open_session() as db:
        sess = db.get(ChatSession, "01ABC")
        assert sess is not None
        assert sess.tgid == "9001"
        assert sess.title is None
        active = db.query(ChatMessage).filter_by(
            session_id="01ABC", archived=0,
        ).order_by(ChatMessage.id).all()
    assert len(active) == 2
    assert active[0].text == "hello"
    assert active[1].text == "hi"

    # Source JSON deleted.
    assert not (workspace / "memories" / "sessions" / "9001" / "01ABC.json").exists()


def test_migrate_imports_archive_with_archived_flag(fresh_db):
    """Archive rows land in ``chat_messages`` with ``archived=1``."""
    from magi.agent.memory.session import migrate_from_json
    from magi.agent.db import ChatMessage, open_session

    state, workspace = fresh_db
    _write_legacy_session(
        workspace, "9001", "01ABC",
        messages=[
            {"role": "user", "text": "tail", "ts": "t", "message_id": "m1"},
        ],
        archive=[
            {"role": "user", "text": "old 1", "ts": "t", "message_id": "a1"},
            {"role": "assistant", "text": "old 2", "ts": "t", "message_id": "a2"},
        ],
        active_tail_count=20,
        last_compaction_at="2026-07-02T00:00:01Z",
    )

    migrate_from_json(workspace)

    with open_session() as db:
        active = db.query(ChatMessage).filter_by(
            session_id="01ABC", archived=0,
        ).all()
        archived = db.query(ChatMessage).filter_by(
            session_id="01ABC", archived=1,
        ).order_by(ChatMessage.id).all()
    assert len(active) == 1
    assert len(archived) == 2
    assert {a.text for a in archived} == {"old 1", "old 2"}


def test_migrate_preserves_title_and_compaction_metadata(fresh_db):
    """Header fields (title, last_compaction_at) round-trip."""
    from magi.agent.memory.session import migrate_from_json
    from magi.agent.db import ChatSession, open_session

    state, workspace = fresh_db
    _write_legacy_session(
        workspace, "9001", "01ABC",
        messages=[],
        title="迁移测试",
        last_compaction_at="2026-07-02T00:00:01Z",
    )

    migrate_from_json(workspace)

    with open_session() as db:
        sess = db.get(ChatSession, "01ABC")
    assert sess is not None
    assert sess.title == "迁移测试"
    assert sess.last_compaction_at == "2026-07-02T00:00:01Z"


def test_migrate_multiple_tgids(fresh_db):
    """Multiple tgid subdirs are walked; sessions are
    imported per chat."""
    from magi.agent.memory.session import migrate_from_json
    from magi.agent.db import ChatSession, open_session

    state, workspace = fresh_db
    _write_legacy_session(workspace, "9001", "01AAA",
                          messages=[{"role": "user", "text": "a",
                                     "ts": "t", "message_id": "a1"}])
    _write_legacy_session(workspace, "9002", "02BBB",
                          messages=[{"role": "user", "text": "b",
                                     "ts": "t", "message_id": "b1"}])

    stats = migrate_from_json(workspace)
    assert stats["imported"] == 2

    with open_session() as db:
        rows = db.query(ChatSession).all()
    assert {r.session_id for r in rows} == {"01AAA", "02BBB"}
    assert {r.tgid for r in rows} == {"9001", "9002"}


def test_migrate_no_json_dir_is_noop(fresh_db):
    """No legacy tree → ``(0, 0, 0)`` and no DB writes."""
    from magi.agent.memory.session import migrate_from_json
    from magi.agent.db import ChatSession, open_session

    state, workspace = fresh_db
    # Don't create any JSON.
    stats = migrate_from_json(workspace)
    assert stats == {"imported": 0, "skipped": 0, "corrupt": 0}
    with open_session() as db:
        assert db.query(ChatSession).count() == 0


# ────────────────────────────────────────────────────────────────── #
# idempotence
# ────────────────────────────────────────────────────────────────── #


def test_migrate_is_idempotent(fresh_db):
    """Second call on the same tree is a no-op — the unique
    constraint on ``(session_id, message_id)`` rejects dupes."""
    from magi.agent.memory.session import migrate_from_json
    from magi.agent.db import ChatMessage, ChatSession, open_session

    state, workspace = fresh_db
    _write_legacy_session(workspace, "9001", "01ABC",
                          messages=[{"role": "user", "text": "x",
                                     "ts": "t", "message_id": "m1"}])

    stats1 = migrate_from_json(workspace)
    assert stats1["imported"] == 1

    # Second call: nothing left to import (JSONs gone).
    stats2 = migrate_from_json(workspace)
    assert stats2["imported"] == 0

    with open_session() as db:
        assert db.query(ChatSession).count() == 1
        assert db.query(ChatMessage).count() == 1


def test_migrate_idempotent_when_json_left_in_place(fresh_db, monkeypatch):
    """If a previous run imported but failed to delete (e.g.
    the unlink raised), the next run re-processes the JSON
    file. ``INSERT OR IGNORE`` keeps the rows unique; the
    second pass tries to insert, the PK collides, no new
    row appears, the JSON gets deleted on this pass.

    The stats counter ``imported`` increments per *attempt*
    that completed successfully (the insert was a no-op due
    to OR IGNORE, but the file got deleted), so we count
    this as ``imported=1`` on the second pass — same total
    rows before and after.
    """
    from magi.agent.memory.session import migrate_from_json
    from magi.agent.db import ChatSession, open_session
    from pathlib import Path

    state, workspace = fresh_db
    path = _write_legacy_session(
        workspace, "9001", "01ABC",
        messages=[{"role": "user", "text": "x", "ts": "t", "message_id": "m1"}],
    )

    # First run: monkey-patch unlink to be a no-op so the JSON
    # remains in place. Row IS inserted (because INSERT OR
    # IGNORE only conflicts on a prior row, and this is the
    # first run).
    real_unlink = Path.unlink
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **kw: None)
    migrate_from_json(workspace)
    with open_session() as db:
        assert db.query(ChatSession).count() == 1

    # Restore unlink; second run sees the JSON still there
    # but the row is already there — INSERT OR IGNORE skips it
    # silently, and this run cleans up the file. Row count
    # stays at 1 (no duplicates, no corruption).
    monkeypatch.setattr(Path, "unlink", real_unlink)
    migrate_from_json(workspace)
    assert not path.exists()
    with open_session() as db:
        assert db.query(ChatSession).count() == 1


# ────────────────────────────────────────────────────────────────── #
# corruption handling
# ────────────────────────────────────────────────────────────────── #


def test_migrate_corrupt_file_is_logged_and_left_in_place(fresh_db, caplog):
    """A malformed JSON file is logged at WARNING, NOT deleted,
    and the rest of the tree still imports."""
    import logging
    from magi.agent.memory.session import migrate_from_json
    from magi.agent.db import ChatSession, open_session

    state, workspace = fresh_db

    # One valid file + one corrupt file.
    _write_legacy_session(workspace, "9001", "01OK",
                          messages=[{"role": "user", "text": "x",
                                     "ts": "t", "message_id": "m1"}])
    bad_path = workspace / "memories" / "sessions" / "9001" / "01BAD.json"
    bad_path.write_text("{ not valid json", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="magi.agent.memory.session"):
        stats = migrate_from_json(workspace)

    # The good one imported; the corrupt one was logged +
    # skipped (counter) but NOT deleted.
    assert stats["imported"] == 1
    assert stats["corrupt"] == 1
    assert bad_path.exists()

    with open_session() as db:
        assert db.query(ChatSession).count() == 1
        assert db.get(ChatSession, "01BAD") is None


def test_migrate_invalid_tgid_dir_is_skipped(fresh_db):
    """A directory whose name violates the tgid regex is
    skipped with a warning (logged) rather than crashing the
    whole migration."""
    from magi.agent.memory.session import migrate_from_json

    state, workspace = fresh_db
    # Create a chat dir with a name outside the regex
    # (contains ``..``).
    bad_dir = workspace / "memories" / "sessions" / "..bad.."
    bad_dir.mkdir(parents=True)
    (bad_dir / "01ABC.json").write_text("{}", encoding="utf-8")

    stats = migrate_from_json(workspace)
    # The bad chat dir is skipped — counted as ``corrupt`` so
    # the operator sees something happened.
    assert stats["corrupt"] == 1
    # The bad dir is not deleted (we don't touch a path
    # outside the tgid regex).
    assert bad_dir.exists()


def test_migrate_cleans_up_empty_chat_dirs(fresh_db):
    """After all the JSON files inside a chat dir are
    imported, the empty parent directory is removed so the
    layout collapses to nothing."""
    from magi.agent.memory.session import migrate_from_json

    state, workspace = fresh_db
    _write_legacy_session(workspace, "9001", "01ABC",
                          messages=[{"role": "user", "text": "x",
                                     "ts": "t", "message_id": "m1"}])

    migrate_from_json(workspace)

    chat_dir = workspace / "memories" / "sessions" / "9001"
    assert not chat_dir.exists()