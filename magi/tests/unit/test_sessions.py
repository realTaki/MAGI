"""In-process tests for :mod:`magi.runtime.sessions`.

These run against a temp workspace (set via ``MAGI_WORKSPACE_DIR``
so ``workspace_root`` puts the session files under the
pytest-provided tmp directory). No DB, no FastAPI, no
async — purely filesystem.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from magi.runtime.sessions import (
    SCHEMA_VERSION,
    SessionCorruptError,
    SessionMessage,
    SessionNotFoundError,
    SessionPathError,
    SessionStore,
    new_session_id,
    session_dir,
    session_path,
    summary_from_session,
)


# Crockford base32 alphabet — used to assert ULID shape.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


@pytest.fixture
def store(tmp_path, monkeypatch) -> SessionStore:
    """A SessionStore rooted at the pytest tmp_path.

    The SessionStore resolves the workspace via
    ``workspace_root(state_dir)`` which defaults to
    ``state_dir.parent``. We override ``MAGI_WORKSPACE_DIR``
    so the workspace *is* ``tmp_path`` — that way every
    created session file lands inside the test's temp
    directory and pytest cleans it up automatically.
    """
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(tmp_path))
    return SessionStore(tmp_path)


# --------------------------------------------------------------------------- #
# helpers / fixtures
# --------------------------------------------------------------------------- #


def _msg(role: str, text: str = "hi", ts: str = "2026-07-03T00:00:00Z") -> SessionMessage:
    return SessionMessage(
        role=role, text=text, ts=ts, message_id=new_session_id()
    )


# --------------------------------------------------------------------------- #
# 1. create persists
# --------------------------------------------------------------------------- #


def test_create_persists(tmp_path, store):
    """``create`` writes the JSON at the canonical path."""
    s = store.create("12345", employee_id=7)
    expected = session_path(tmp_path, "12345", s.session_id)
    assert expected.is_file()


def test_create_chat_id_validation(store):
    """chat_ids that would escape the path dir raise ``SessionPathError``."""
    with pytest.raises(SessionPathError):
        store.create("../etc", employee_id=1)
    with pytest.raises(SessionPathError):
        store.create("a/b", employee_id=1)
    with pytest.raises(SessionPathError):
        store.create("", employee_id=1)


def test_session_id_safe_via_path(store):
    """session_id must match the ULID shape."""
    s = store.create("124", employee_id=1)
    with pytest.raises(SessionPathError):
        store.append_messages("124", "../bad", [_msg("user")])


# --------------------------------------------------------------------------- #
# 2. round-trip
# --------------------------------------------------------------------------- #


def test_get_round_trip(store):
    """``get`` returns what ``create`` wrote, and round-trips through
    ``append_messages`` for new messages."""
    s = store.create("124", employee_id=7)
    fetched = store.get("124", s.session_id)
    assert fetched == s
    assert fetched.messages == []


def test_append_and_get(store):
    """``append_messages`` adds and persists, and ``get`` sees the result."""
    s = store.create("124", employee_id=7)
    msgs = [_msg("user", "hello"), _msg("assistant", "hi back")]
    out = store.append_messages("124", s.session_id, msgs)
    assert out.messages == msgs
    assert out.session_id == s.session_id
    assert out.created_at == s.created_at  # append bumps updated_at, not created_at
    assert out.updated_at >= s.updated_at


def test_append_to_missing_raises(store):
    """Appending to a non-existent session raises SessionNotFoundError."""
    with pytest.raises(SessionNotFoundError):
        store.append_messages("124", new_session_id(), [_msg("user")])


# --------------------------------------------------------------------------- #
# 3. delete
# --------------------------------------------------------------------------- #


def test_delete_idempotent(store):
    """``delete`` returns True the first time, False after."""
    s = store.create("124", employee_id=7)
    assert store.delete("124", s.session_id) is True
    assert store.get("124", s.session_id) is None
    assert store.delete("124", s.session_id) is False


# --------------------------------------------------------------------------- #
# 4. pagination
# --------------------------------------------------------------------------- #


def test_list_summaries_paginates(store):
    """``limit + offset`` slices the sorted list correctly."""
    created = [store.create("124", employee_id=1) for _ in range(5)]
    # All 5 share the same millisecond timestamp — they tie
    # on the primary sort. Order is consistent within a
    # call, though, so we can still test the slicing.
    page1, total = store.list_summaries("124", limit=2, offset=0)
    page2, total2 = store.list_summaries("124", limit=2, offset=2)
    page3, total3 = store.list_summaries("124", limit=2, offset=4)
    assert total == total2 == total3 == 5
    assert len(page1) == 2
    assert len(page2) == 2
    assert len(page3) == 1
    # Distinct ids across pages.
    seen = {s.session_id for s in (page1 + page2 + page3)}
    assert seen == {s.session_id for s in created}


def test_list_summaries_empty(store):
    """No sessions yet → empty list + total 0."""
    items, total = store.list_summaries("nope")
    assert items == [] and total == 0


# --------------------------------------------------------------------------- #
# 5. cross-chat isolation
# --------------------------------------------------------------------------- #


def test_chat_ids_isolated(store):
    """Two chat_ids do not see each other's sessions."""
    a = store.create("aaa", employee_id=1)
    b = store.create("bbb", employee_id=2)
    assert store.get("aaa", a.session_id) is not None
    assert store.get("bbb", b.session_id) is not None
    # a's session id is unreachable from b's chat_id.
    assert store.get("bbb", a.session_id) is None  # file doesn't exist there
    with pytest.raises(SessionNotFoundError):
        store.append_messages("bbb", a.session_id, [_msg("user")])
    # And b's session id is unreachable from a's chat_id.
    assert store.get("aaa", b.session_id) is None
    # Sanity: nothing accidentally wrote into a's directory.
    assert {s.session_id for s in store.list_summaries("aaa")[0]} == {a.session_id}
    assert {s.session_id for s in store.list_summaries("bbb")[0]} == {b.session_id}


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
    # 5ms tick is enough to bump the timestamp portion.
    import time as _t
    _t.sleep(0.005)
    b = new_session_id()
    assert a < b


# --------------------------------------------------------------------------- #
# 7. atomic write
# --------------------------------------------------------------------------- #


def test_atomic_write_no_partial(store, tmp_path):
    """The on-disk file is never half-written.

    We trigger a write that would produce malformed JSON
    by monkeypatching ``json.dump`` to raise mid-write.
    After the exception, the previous valid contents must
    still be on disk (target file untouched).
    """
    s = store.create("124", employee_id=7)
    original = session_path(tmp_path, "124", s.session_id).read_text()

    import json as _json
    real_dump = _json.dump

    def boom(*a, **kw):
        # Drop the second call only — leaves a half-written temp.
        # os.replace never runs because the exception escapes.
        raise RuntimeError("simulated mid-write")

    import magi.runtime.sessions as _s
    _s.json.dump = boom
    try:
        with pytest.raises(RuntimeError):
            store.append_messages("124", s.session_id, [_msg("user")])
    finally:
        _s.json.dump = real_dump

    # Target file intact.
    assert session_path(tmp_path, "124", s.session_id).read_text() == original
    # No leftover .tmp.* files in the chat dir.
    leftover = list(Path(store._workspace / "memories" / "sessions" / "124").glob(".tmp.*"))
    assert leftover == [], f"leftover temp files: {leftover}"


# --------------------------------------------------------------------------- #
# 8. corrupt-on-disk
# --------------------------------------------------------------------------- #


def test_get_corrupt_file_raises(store, tmp_path):
    """A malformed JSON file raises SessionCorruptError, not None."""
    s = store.create("124", employee_id=7)
    session_path(tmp_path, "124", s.session_id).write_text("{ not valid json")
    with pytest.raises(SessionCorruptError):
        store.get("124", s.session_id)


def test_summary_preview_falls_back_when_no_user_message(store, tmp_path):
    """``preview`` is empty when only assistant messages exist."""
    s = store.create("124", employee_id=7)
    store.append_messages("124", s.session_id, [
        SessionMessage(message_id=new_session_id(), role="assistant",
                      text="hi", ts="2026-07-03T00:00:00Z"),
    ])
    items, _ = store.list_summaries("124")
    assert items[0].preview == ""


def test_schema_version_mismatch_raises(store):
    """A v2 file raises SessionCorruptError on read (we know v1 only)."""
    s = store.create("124", employee_id=7)
    # Manually bump the schema_version on disk to a value
    # that doesn't match v1.
    path = session_path(Path(store._workspace), "124", s.session_id)
    data = json.loads(path.read_text())
    data["schema_version"] = 99
    path.write_text(json.dumps(data))

    with pytest.raises(SessionCorruptError):
        store.get("124", s.session_id)


def test_summary_from_session_truncates(store):
    """Preview is trimmed to the _PREVIEW_CHARS limit."""
    long_text = "a" * 200
    s = store.create("124", employee_id=7)
    store.append_messages("124", s.session_id,
                         [_msg("user", long_text)])
    items, _ = store.list_summaries("124")
    assert items[0].preview.endswith("…")
    assert len(items[0].preview) == 80 + 1  # 80 chars + ellipsis


def test_session_dir_helper(tmp_path):
    d = session_dir(tmp_path, "alice")
    assert d == tmp_path / "memories" / "sessions" / "alice"
