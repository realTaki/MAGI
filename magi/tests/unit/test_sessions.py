"""In-process tests for :mod:`magi.agent.memory.session` (D.18+).

D.18 moved sessions from per-file JSON into two SQLAlchemy
tables (``chat_sessions`` + ``chat_messages``). The tests below
exercise the new SQLite-backed ``SessionStore`` against a fresh
in-memory-ish DB (per-test tmp dir + engine reset). No FastAPI,
no async — purely the storage layer.

Tests that pinned the pre-D.18 on-disk shape (atomic write,
corrupt-file detection, ``session_path``/``session_dir``
helpers, per-session ``asyncio.Lock`` serialisation) were
dropped: those contracts no longer apply once SQLite handles
atomicity at the row level.

D.23 — the session key changed from ``tgid`` to
``uid``. The tests below were rewritten to use
``uid`` as the first argument; the legacy
``tgid`` parameter on ``create`` is preserved (it's the
per-channel delivery address stored on the row's ``tgid``
column) but it's no longer the lookup key.
"""

from __future__ import annotations

import pytest

from magi.agent.memory.session import (
    Session,
    SessionMessage,
    SessionStore,
    new_session_id,
    summary_from_session,
)


# Crockford base32 alphabet — used to assert ULID shape.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    """Per-test isolated state dir + fresh ORM engine.

    Resets the process-wide ``orm._engine`` singleton so each
    test gets its own DB at ``tmp_path / state / magi.db``.
    Sessions writes target this DB; the SessionStore wraps it.
    """
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

    return state


@pytest.fixture
def store(fresh_db) -> SessionStore:
    """SessionStore pointing at the per-test DB."""
    return SessionStore(str(fresh_db))


def _msg(role: str, text: str = "hi", ts: str = "2026-07-03T00:00:00Z") -> SessionMessage:
    return SessionMessage(
        role=role, text=text, ts=ts, message_id=new_session_id()
    )


# --------------------------------------------------------------------------- #
# 1. create persists
# --------------------------------------------------------------------------- #


def test_create_persists(store):
    """``create`` returns a populated Session and ``get`` sees it."""
    from magi.agent.db import ChatSession, open_session

    s = store.create(7, )
    # Session row landed in the DB.
    with open_session() as db:
        row = db.get(ChatSession, s.session_id)
    assert row is not None
    assert row.tgid == "12345"
    assert row.uid == 7
    assert row.title is None


def test_create_employee_id_validation(store):
    """Non-integer employee_ids raise ``ValueError``."""
    with pytest.raises(ValueError):
        store.create("not-a-number")
    with pytest.raises(ValueError):
        store.create("../etc")
    with pytest.raises(ValueError):
        store.create(-1)


def test_session_id_safe_via_path(store):
    """``append_messages`` rejects an obviously bad session_id
    with ``SessionPathError`` (the API-layer error-mapping
    contract). A valid ULID-shape session_id that just doesn't
    exist raises ``SessionNotFoundError`` instead.
    """
    from magi.agent.memory.session import SessionNotFoundError, SessionPathError
    s = store.create(1, )
    # Bad shape → ``SessionPathError`` (the shape guard).
    with pytest.raises(SessionPathError):
        store.append_messages(1, "../bad", [_msg("user")])
    # Valid ULID-shape but no such session → ``SessionNotFoundError``.
    with pytest.raises(SessionNotFoundError):
        store.append_messages(1, new_session_id(), [_msg("user")])


# --------------------------------------------------------------------------- #
# 2. round-trip
# --------------------------------------------------------------------------- #


def test_get_round_trip(store):
    """``get`` returns what ``create`` wrote."""
    s = store.create(7, )
    fetched = store.get(7, s.session_id)
    assert fetched == s
    assert fetched.messages == []


def test_append_and_get(store):
    """``append_messages`` adds and persists, and ``get`` sees the result."""
    s = store.create(7, )
    msgs = [_msg("user", "hello"), _msg("assistant", "hi back")]
    out = store.append_messages(7, s.session_id, msgs)
    assert out.messages == msgs
    assert out.session_id == s.session_id
    assert out.created_at == s.created_at
    assert out.updated_at >= s.updated_at


def test_append_to_missing_raises(store):
    """Appending to a non-existent session raises SessionNotFoundError."""
    from magi.agent.memory.session import SessionNotFoundError
    with pytest.raises(SessionNotFoundError):
        store.append_messages(124, new_session_id(), [_msg("user")])


def test_append_validates_role(store):
    """Bad role values are rejected before any DB write."""
    from magi.agent.memory.session import SessionCorruptError
    s = store.create(7, )
    bad = SessionMessage(role="admin", text="x", ts="t", message_id=new_session_id())
    with pytest.raises(SessionCorruptError):
        store.append_messages(7, s.session_id, [bad])


# --------------------------------------------------------------------------- #
# 3. delete
# --------------------------------------------------------------------------- #


def test_delete_idempotent(store):
    """``delete`` returns True the first time, False after."""
    from magi.agent.db import ChatSession, open_session
    s = store.create(7, )
    assert store.delete(7, s.session_id) is True
    with open_session() as db:
        assert db.get(ChatSession, s.session_id) is None
    assert store.get(7, s.session_id) is None
    assert store.delete(7, s.session_id) is False


def test_delete_cascades_to_messages(store):
    """Deleting a session also clears its message rows."""
    from magi.agent.db import ChatMessage, open_session
    s = store.create(7, )
    store.append_messages(7, s.session_id, [_msg("user"), _msg("assistant")])
    store.delete(7, s.session_id)
    with open_session() as db:
        remaining = db.query(ChatMessage).filter_by(session_id=s.session_id).all()
    assert remaining == []


# --------------------------------------------------------------------------- #
# 4. pagination
# --------------------------------------------------------------------------- #


def test_list_summaries_paginates(store):
    """``limit + offset`` slices the sorted list correctly."""
    created = [store.create(1) for _ in range(5)]
    page1, total = store.list_summaries(1, limit=2, offset=0)
    page2, total2 = store.list_summaries(1, limit=2, offset=2)
    page3, total3 = store.list_summaries(1, limit=2, offset=4)
    assert total == total2 == total3 == 5
    assert len(page1) == 2
    assert len(page2) == 2
    assert len(page3) == 1
    seen = {s.session_id for s in (page1 + page2 + page3)}
    assert seen == {s.session_id for s in created}


def test_list_summaries_empty(store):
    """No sessions yet → empty list + total 0."""
    items, total = store.list_summaries(999)
    assert items == [] and total == 0


def test_list_summaries_preview_truncates(store):
    """Preview is trimmed to ``_PREVIEW_CHARS`` with trailing ellipsis."""
    long_text = "a" * 200
    s = store.create(7, )
    store.append_messages(7, s.session_id, [_msg("user", long_text)])
    items, _ = store.list_summaries(7)
    assert items[0].preview.endswith("…")
    assert len(items[0].preview) == 80 + 1


def test_list_summaries_message_count_excludes_archive(store):
    """``message_count`` is the active-only count; archived rows
    rolled out by compaction are excluded."""
    s = store.create(7, )
    msgs = [_msg("user", f"msg {i}") for i in range(4)]
    store.append_messages(7, s.session_id, msgs)
    # Manually flip two rows to archived=1 to simulate a
    # compaction pass.
    from magi.agent.db import ChatMessage, open_session
    with open_session() as db:
        rows = db.query(ChatMessage).filter_by(session_id=s.session_id).all()
        for r in rows[:2]:
            r.archived = 1
        db.commit()

    items, _ = store.list_summaries(7)
    assert items[0].message_count == 2


# --------------------------------------------------------------------------- #
# 5. cross-employee isolation (DB-side WHERE uid = ?)
# --------------------------------------------------------------------------- #


def test_employee_ids_isolated(store):
    """Two employees do not see each other's sessions."""
    from magi.agent.memory.session import SessionNotFoundError
    a = store.create(1, )
    b = store.create(2, )
    assert store.get(1, a.session_id) is not None
    assert store.get(2, b.session_id) is not None
    # a's session id is unreachable from employee 2.
    assert store.get(2, a.session_id) is None
    with pytest.raises(SessionNotFoundError):
        store.append_messages(2, a.session_id, [_msg("user")])
    # And b's session id is unreachable from employee 1.
    assert store.get(1, b.session_id) is None
    # Sanity: list_summaries scopes by uid.
    assert {s.session_id for s in store.list_summaries(1)[0]} == {a.session_id}
    assert {s.session_id for s in store.list_summaries(2)[0]} == {b.session_id}


# --------------------------------------------------------------------------- #
# 6. ULID shape
# --------------------------------------------------------------------------- #


def test_session_id_is_ulid():
    """ULID is 26 chars, Crockford base32."""
    for _ in range(20):
        sid = new_session_id()
        assert len(sid) == 26
        for c in sid:
            assert c in _CROCKFORD


def test_ulid_lexicographic_order():
    """ULIDs created later sort greater — they encode the timestamp."""
    a = new_session_id()
    import time as _t
    _t.sleep(0.005)
    b = new_session_id()
    assert a < b


# --------------------------------------------------------------------------- #
# 7. summary / preview fallbacks
# --------------------------------------------------------------------------- #


def test_summary_preview_falls_back_when_no_user_message(store):
    """``preview`` is empty when only assistant messages exist."""
    s = store.create(7, )
    store.append_messages(7, s.session_id, [
        SessionMessage(message_id=new_session_id(), role="assistant",
                      text="hi", ts="2026-07-03T00:00:00Z"),
    ])
    items, _ = store.list_summaries(7)
    assert items[0].preview == ""


def test_summary_from_session_truncates():
    """Standalone ``summary_from_session`` preview helper."""
    s = Session(
        session_id="01ABC",
        tgid="12345",
        uid=1,
        channel="webui",
        created_at="t", updated_at="t",
        messages=[
            SessionMessage(message_id="m1", role="user", text="a" * 200, ts="t"),
        ],
    )
    summary = summary_from_session(s)
    assert summary.preview.endswith("…")
    assert len(summary.preview) == 80 + 1
    assert summary.message_count == 1


# --------------------------------------------------------------------------- #
# 8. rename
# --------------------------------------------------------------------------- #


def test_rename_happy_path(store):
    """``rename`` writes the new title and ``get`` sees it on the next read."""
    s = store.create(1, )
    out = store.rename(1, s.session_id, "Acme 会议 明天 3 点")
    assert out.title == "Acme 会议 明天 3 点"
    again = store.get(1, s.session_id)
    assert again.title == "Acme 会议 明天 3 点"


def test_rename_trims_and_clamps(store):
    """Whitespace stripped; over-length input clamped at 80 chars."""
    s = store.create(1, )
    out = store.rename(1, s.session_id, "   hello world   ")
    assert out.title == "hello world"
    out = store.rename(1, s.session_id, "x" * 200)
    assert len(out.title) == 80


def test_rename_clear(store):
    """``None`` and empty string both clear the title."""
    s = store.create(1, )
    store.rename(1, s.session_id, "temp")
    assert store.get(1, s.session_id).title == "temp"
    store.rename(1, s.session_id, None)
    assert store.get(1, s.session_id).title is None
    store.rename(1, s.session_id, "temp again")
    store.rename(1, s.session_id, "")
    assert store.get(1, s.session_id).title is None


def test_rename_missing_session_raises_not_found(store):
    from magi.agent.memory.session import SessionNotFoundError
    with pytest.raises(SessionNotFoundError):
        store.rename(1, new_session_id(), "orphan")


def test_rename_does_not_bump_updated_at_when_disabled(store):
    """``bump_updated=False`` keeps ``updated_at`` frozen."""
    import time as _t
    s = store.create(1, )
    initial = s.updated_at
    _t.sleep(0.005)
    out = store.rename(1, s.session_id, "x", bump_updated=False)
    assert out.updated_at == initial


def test_rename_bumps_updated_at_by_default(store):
    import time as _t
    s = store.create(1, )
    initial = s.updated_at
    _t.sleep(0.005)
    out = store.rename(1, s.session_id, "x")
    assert out.updated_at > initial


def test_summary_includes_title(store):
    s = store.create(1, )
    store.append_messages(1, s.session_id, [_msg("user", "first message")])
    store.rename(1, s.session_id, "Renamed")
    items, _ = store.list_summaries(1)
    assert items[0].title == "Renamed"
    assert items[0].preview == "first message"


# --------------------------------------------------------------------------- #
# 9. set_title_if_null — D.18 compare-and-set
# --------------------------------------------------------------------------- #


def test_set_title_if_null_succeeds_when_title_unset(store):
    """First writer wins when title is still NULL."""
    s = store.create(1, )
    out = store.set_title_if_null(1, s.session_id, "auto")
    assert out is not None
    assert out.title == "auto"


def test_set_title_if_null_loses_when_title_set(store):
    """Second writer is rejected when title is already set."""
    s = store.create(1, )
    store.rename(1, s.session_id, "manual")
    out = store.set_title_if_null(1, s.session_id, "auto")
    assert out is None
    # The manual title is preserved.
    assert store.get(1, s.session_id).title == "manual"


def test_set_title_if_null_returns_none_for_missing_session(store):
    out = store.set_title_if_null(1, new_session_id(), "x")
    assert out is None


def test_set_title_if_null_clamps_long_titles(store):
    """Title is length-clamped at 80 chars (same as rename)."""
    s = store.create(1, )
    out = store.set_title_if_null(1, s.session_id, "x" * 200)
    assert out is not None
    assert len(out.title) == 80


# --------------------------------------------------------------------------- #
# 10. session_lock shim — post-D.18 it's a no-op
# --------------------------------------------------------------------------- #


def test_session_lock_is_now_a_noop():
    """Pre-D.18 ``session_lock`` returned a per-session
    ``asyncio.Lock``. D.18 removed that machinery (SQLite's
    per-statement atomicity + the ``set_title_if_null``
    compare-and-set replace it). The shim must still be
    callable without raising — callers that haven't been
    migrated yet (none should remain, but the contract is
    explicitly a no-op) get a safe AttributeError-free no-op.
    """
    from magi.agent.memory.session import session_lock
    assert session_lock("any-chat", "any-session") is None


# --------------------------------------------------------------------------- #
# 11. ChatSession.__repr__ doesn't blow up on the missing tgid attr
# --------------------------------------------------------------------------- #


def test_chatsession_repr_uses_tgid(store):
    """``ChatSession`` has a ``tgid`` column (D.23 renamed the
    per-channel delivery address from ``tgid`` to ``tgid``).
    A previous ``__repr__`` mistakenly referenced
    ``self.tgid`` and crashed with ``AttributeError`` the
    first time anything tried to log or debug-print a
    session row. The fixed repr must round-trip via the
    real column name."""
    from magi.agent.db import ChatSession, open_session

    s = store.create(1, tgid="9001")
    with open_session() as db:
        row = db.get(ChatSession, s.session_id)
    assert row is not None
    text = repr(row)
    # The repr must mention the tgid we set, not crash, and
    # not silently drop to a half-formed string.
    assert "tgid=9001" in text
    assert "session_id=" in text
    assert "title=" in text
    # And it must not still be referencing the old field name.
    assert "chat_id" not in text