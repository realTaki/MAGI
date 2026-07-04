"""Tests for the Telegram channel's session lifecycle.

The TG handler writes a file per ``(chat_id, session_id)``
under ``<workspace>/memories/sessions/<chat_id>/<sid>.json``
mirroring the WebUI chat path. The TG-specific helper
:func:`_resolve_or_create_tg_session` enforces the
"one session per chat_id forever" policy: a repeat call
returns the same id rather than minting a new one.

What we test:

  - first call creates a session and writes a file
    tagged with ``channel="tg"``
  - second call returns the *same* id
  - a manually-deleted session file triggers creation of
    a fresh one on the next call
  - a corrupt session file is treated like "no session"
    (skipped, fresh one minted) — the inbound handler
    must not crash on a bad file
  - different chat_ids don't share sessions (path-level
    isolation; mirrors the WebUI test)
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def tg_session_env(monkeypatch, tmp_path):
    """Pin ``MAGI_STATE_DIR`` + ``MAGI_WORKSPACE_DIR`` to a
    tmp-scoped location so each test starts clean and the
    workspace path doesn't collide with the host's actual
    ``/workspace``.

    The :func:`workspace_root` derivation puts the workspace
    at ``<state_dir>.parent`` unless ``MAGI_WORKSPACE_DIR``
    is set; we set it explicitly to ``<tmp_path>`` so the
    session files land under ``<tmp_path>/memories/sessions/``.
    """
    state = tmp_path / "memories"
    state.mkdir()
    workspace = tmp_path
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(workspace))
    return state, workspace


# -- happy path ----------------------------------------------------------


def test_first_call_creates_session(tg_session_env):
    """No prior session on disk → helper mints a fresh one."""
    from magi.channels.telegram.bot import _resolve_or_create_tg_session
    from magi.runtime.sessions import SessionStore

    state_dir, workspace = tg_session_env
    store = SessionStore(state_dir)

    sid = _resolve_or_create_tg_session(store, "6240201712", employee_id=42)

    # ULID-shaped: 26 Crockford base32 chars.
    assert isinstance(sid, str)
    assert len(sid) == 26

    # File landed under the chat_id's session dir and is
    # tagged ``channel="tg"`` (so a future replay tool can
    # tell which surface produced each session).
    sess_dir = workspace / "memories" / "sessions" / "6240201712"
    assert sess_dir.is_dir()
    files = list(sess_dir.glob("*.json"))
    assert len(files) == 1
    assert files[0].stem == sid

    # The on-disk record carries channel="tg" so analytics
    # can split TG vs WebUI threads.
    import json
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["channel"] == "tg"
    assert data["chat_id"] == "6240201712"
    assert data["employee_id"] == 42


def test_second_call_reuses_same_session(tg_session_env):
    """Subsequent messages in the same TG chat append to
    the existing file rather than spawning a new thread.

    This is the core "one session per chat_id forever" policy.
    """
    from magi.channels.telegram.bot import _resolve_or_create_tg_session
    from magi.runtime.sessions import SessionStore

    state_dir, workspace = tg_session_env
    store = SessionStore(state_dir)

    sid1 = _resolve_or_create_tg_session(store, "6240201712", employee_id=42)
    sid2 = _resolve_or_create_tg_session(store, "6240201712", employee_id=42)
    sid3 = _resolve_or_create_tg_session(store, "6240201712", employee_id=42)

    assert sid1 == sid2 == sid3, "TG must reuse one session per chat_id"

    # Still only one file on disk.
    sess_dir = workspace / "memories" / "sessions" / "6240201712"
    files = list(sess_dir.glob("*.json"))
    assert len(files) == 1


# -- resilience ----------------------------------------------------------


def test_call_after_session_deleted_mints_fresh(tg_session_env):
    """If the operator wiped the session file (manual ``rm``,
    wiped volume, etc.), the next inbound message creates
    a brand-new session rather than crashing.

    Real-world equivalent: someone runs ``rm -rf
    /workspace/memories/sessions/6240201712`` to clear
    history. The next TG message should not 500.
    """
    from magi.channels.telegram.bot import _resolve_or_create_tg_session
    from magi.runtime.sessions import SessionStore

    state_dir, workspace = tg_session_env
    store = SessionStore(state_dir)

    sid1 = _resolve_or_create_tg_session(store, "6240201712", employee_id=42)
    # Operator wipes the session file.
    sess_dir = workspace / "memories" / "sessions" / "6240201712"
    for f in sess_dir.glob("*.json"):
        f.unlink()
    assert list(sess_dir.glob("*.json")) == []

    sid2 = _resolve_or_create_tg_session(store, "6240201712", employee_id=42)
    assert sid1 != sid2, "after wipe, helper should mint a fresh id"


def test_call_with_corrupt_session_file_mints_fresh(tg_session_env):
    """A truncated / unparseable session JSON file is skipped
    and a fresh session is created. The handler must not
    raise into the inbound path.

    The WebUI chat_sessions tests already pin this for the
    ``GET`` path; the TG helper does its own check via
    ``store.get`` so a corrupt file from outside the TG
    path (manual edit, mid-write crash) doesn't crash
    the next inbound message.
    """
    from magi.channels.telegram.bot import _resolve_or_create_tg_session
    from magi.runtime.sessions import SessionStore

    state_dir, workspace = tg_session_env
    store = SessionStore(state_dir)

    # Seed a corrupt file under the chat_id dir.
    sess_dir = workspace / "memories" / "sessions" / "6240201712"
    sess_dir.mkdir(parents=True, exist_ok=True)
    corrupt = sess_dir / "01ABCDEFGHJKMNPQRSTVWXYZAB.json"
    corrupt.write_text('{"schema_version": 1, "broken": ', encoding="utf-8")

    # Helper should NOT raise; it should mint a fresh id.
    sid = _resolve_or_create_tg_session(store, "6240201712", employee_id=42)
    assert isinstance(sid, str) and len(sid) == 26
    # The corrupt file is still on disk (the helper doesn't
    # clean it up — a future "delete corrupt files" sweep
    # could, but it's out of scope here).
    assert corrupt.exists()


# -- isolation -----------------------------------------------------------


def test_different_chat_ids_get_different_sessions(tg_session_env):
    """Two employees chatting this EVE get two distinct files —
    path-level isolation mirrors the WebUI guarantee so
    one user's history never bleeds into another's.
    """
    from magi.channels.telegram.bot import _resolve_or_create_tg_session
    from magi.runtime.sessions import SessionStore

    state_dir, workspace = tg_session_env
    store = SessionStore(state_dir)

    sid_a = _resolve_or_create_tg_session(store, "6240201712", employee_id=1)
    sid_b = _resolve_or_create_tg_session(store, "9876543210", employee_id=2)

    assert sid_a != sid_b

    sessions_root = workspace / "memories" / "sessions"
    assert (sessions_root / "6240201712").is_dir()
    assert (sessions_root / "9876543210").is_dir()
    # Each chat_id has exactly one file.
    assert len(list((sessions_root / "6240201712").glob("*.json"))) == 1
    assert len(list((sessions_root / "9876543210").glob("*.json"))) == 1


# -- integration with SessionStore.append_messages -----------------------


def test_messages_persist_to_file(tg_session_env):
    """End-to-end: helper mints a session, the handler (or
    anything using SessionStore.append_messages) writes
    user + assistant rows, and the file reflects them.

    This is the contract the real TG handler relies on;
    the test exercises the same code paths without
    spinning up python-telegram-bot's Update machinery.
    """
    from magi.channels.telegram.bot import _resolve_or_create_tg_session
    from magi.runtime.sessions import (
        SessionMessage,
        SessionStore,
        new_session_id,
    )

    state_dir, _workspace = tg_session_env
    store = SessionStore(state_dir)
    chat_id = "6240201712"

    sid = _resolve_or_create_tg_session(store, chat_id, employee_id=42)

    # Simulate the inbound append + outbound append that
    # the real TG handler does under session_lock.
    store.append_messages(
        chat_id, sid,
        [SessionMessage(
            role="user", text="hello",
            ts="2026-07-03T10:00:00Z",
            message_id=new_session_id(),
        )],
    )
    store.append_messages(
        chat_id, sid,
        [SessionMessage(
            role="assistant", text="hi there",
            ts="2026-07-03T10:00:05Z",
            message_id=new_session_id(),
        )],
    )

    sess = store.get(chat_id, sid)
    assert sess is not None
    assert len(sess.messages) == 2
    assert sess.messages[0].role == "user"
    assert sess.messages[0].text == "hello"
    assert sess.messages[1].role == "assistant"
    assert sess.messages[1].text == "hi there"