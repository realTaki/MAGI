"""Tests for the D.17 auto-compact subsystem.

Three surfaces pinned:

  - Session round-trip via ``session_to_dict`` /
    ``session_from_dict``: the new fields
    (``archive``, ``active_tail_count``,
    ``last_compaction_at``) survive a save/load cycle and
    default sensibly when missing (backward compat with
    pre-D.17 files).

  - ``magi.runtime.tokens.estimate_messages_tokens``:
    the trigger heuristic returns plausible numbers for
    the inputs we expect (long session vs short).

  - ``magi.runtime.agent._build_messages_from_session``:
    loads prior-turn messages into ChatMessage order and
    maps roles correctly (system summary at messages[0]
    becomes a ``user`` ChatMessage so Anthropic's wire
    format accepts it).

The full LLM-trigger loop (``_maybe_compact``,
``_call_llm_for_summary``) is intentionally NOT exercised
in this file — both depend on a live provider, and the
existing ``test_tg_admin_routes`` patches the
``agent.handle_message`` module attribute without using
``monkeypatch``, which leaks across tests in this suite.
Live smoke (real chat turn + a forced threshold) covers
the integration path; this file pins the deterministic
helper surface.
"""

from __future__ import annotations

import json

import pytest


# -- session round-trip --------------------------------------------------


def test_session_to_dict_includes_new_fields():
    """All three new fields are emitted in JSON even
    when set to default values. Otherwise old files
    written without them could lose state on the next
    write."""
    from magi.runtime.sessions import (
        Session,
        SessionMessage,
        session_to_dict,
    )

    s = Session(
        session_id="01ABC",
        chat_id="9001",
        employee_id=2,
        channel="webui",
        created_at="2026-07-01T00:00:00Z",
        updated_at="2026-07-01T00:00:00Z",
        messages=[SessionMessage(
            role="user", text="hi", ts="t", message_id="m1",
        )],
        title=None,
        archive=[],
        active_tail_count=20,
        last_compaction_at=None,
    )
    d = session_to_dict(s)
    assert "archive" in d
    assert "active_tail_count" in d
    assert "last_compaction_at" in d
    assert d["archive"] == []
    assert d["active_tail_count"] == 20
    assert d["last_compaction_at"] is None


def test_session_archive_round_trip():
    """A session with a non-empty archive survives
    to_dict → from_dict with all entries intact."""
    from magi.runtime.sessions import (
        Session,
        SessionMessage,
        session_to_dict,
        session_from_dict,
    )

    archived = [
        SessionMessage(
            role="user", text="old msg 1",
            ts="2026-07-01T00:00:00Z",
            message_id="a1",
        ),
        SessionMessage(
            role="assistant", text="old reply",
            ts="2026-07-01T00:00:01Z",
            message_id="a2",
        ),
    ]
    s = Session(
        session_id="01ABC",
        chat_id="9001",
        employee_id=2,
        channel="webui",
        created_at="t",
        updated_at="t",
        messages=[SessionMessage(
            role="user", text="new msg",
            ts="2026-07-02T00:00:00Z",
            message_id="m2",
        )],
        title=None,
        archive=archived,
        active_tail_count=20,
        last_compaction_at="2026-07-02T00:00:01Z",
    )
    d = session_to_dict(s)
    s2 = session_from_dict(d)
    assert len(s2.archive) == 2
    assert s2.archive[0].text == "old msg 1"
    assert s2.archive[0].role == "user"
    assert s2.archive[1].role == "assistant"
    assert s2.last_compaction_at == "2026-07-02T00:00:01Z"
    assert s2.active_tail_count == 20
    # Active list still has the recent message.
    assert len(s2.messages) == 1
    assert s2.messages[0].text == "new msg"


def test_session_from_dict_backward_compatible():
    """A file written before D.17 (no archive /
    active_tail_count / last_compaction_at keys) loads
    with default values. The schema_version stays 1; no
    migration needed."""
    from magi.runtime.sessions import session_from_dict

    old = {
        "schema_version": 1,
        "session_id": "01ABC",
        "chat_id": "9001",
        "employee_id": 2,
        "channel": "webui",
        "created_at": "t",
        "updated_at": "t",
        "title": None,
        "messages": [],
    }
    s = session_from_dict(old)
    assert s.archive == []
    assert s.active_tail_count == 20
    assert s.last_compaction_at is None


def test_session_active_tail_count_clamped_on_load():
    """A hand-edited ``active_tail_count: 0`` is clamped
    back to 20 (the default) rather than persisting as
    0, which would mean "keep zero messages" — agent
    loop would have nothing to send to the LLM."""
    from magi.runtime.sessions import session_from_dict

    bad = {
        "schema_version": 1,
        "session_id": "01ABC",
        "chat_id": "9001",
        "employee_id": 2,
        "channel": "webui",
        "created_at": "t",
        "updated_at": "t",
        "title": None,
        "messages": [],
        "active_tail_count": 0,
    }
    s = session_from_dict(bad)
    assert s.active_tail_count == 20


def test_session_invalid_archive_role_rejected():
    """An archive entry with role='admin' (not in the
    allowed set) is a hard load error — better to fail
    closed than to silently coerce."""
    from magi.runtime.sessions import SessionCorruptError, session_from_dict

    bad = {
        "schema_version": 1,
        "session_id": "01ABC",
        "chat_id": "9001",
        "employee_id": 2,
        "channel": "webui",
        "created_at": "t",
        "updated_at": "t",
        "title": None,
        "messages": [],
        "archive": [
            {"role": "admin", "text": "x", "ts": "t",
             "message_id": "a1"},
        ],
    }
    with pytest.raises(SessionCorruptError, match="role"):
        session_from_dict(bad)


# -- token estimator -----------------------------------------------------


def test_estimate_string_tokens_basic():
    """4 chars ≈ 1 token. The empty string is 0."""
    from magi.runtime.tokens import estimate_string_tokens

    assert estimate_string_tokens("") == 0
    assert estimate_string_tokens("abcd") == 1
    assert estimate_string_tokens("a" * 400) == 100
    # 7 chars → 1 token (floor).
    assert estimate_string_tokens("a" * 7) == 1


def test_estimate_messages_tokens_handles_text_and_blocks():
    """Each message contributes text chars + per-message
    overhead. ``content_blocks`` JSON adds to the chars."""
    from magi.runtime.llm.provider import ChatMessage
    from magi.runtime.tokens import estimate_messages_tokens

    msgs = [
        ChatMessage(role="user", content="a" * 400),  # 100 text + 4 overhead
        ChatMessage(
            role="assistant",
            content="ok",
            content_blocks=[{"type": "tool_result",
                             "tool_use_id": "t1",
                             "content": "x" * 100,  # 25 text-equivalent
                             "is_error": False}],
        ),
    ]
    tokens = estimate_messages_tokens(msgs)
    # 400 chars text + ~50 chars JSON wrapper around the
    # content_blocks + 8 overhead = ~115; bound loose.
    assert 100 < tokens < 200


def test_estimate_messages_tokens_empty():
    from magi.runtime.llm.provider import ChatMessage
    from magi.runtime.tokens import estimate_messages_tokens

    assert estimate_messages_tokens([]) == 0


# -- _build_messages_from_session ------------------------------------------


def test_build_messages_from_session_no_session_returns_one_user_msg(
    monkeypatch, tmp_path,
):
    """First turn of a brand-new conversation has no
    session yet → just the user message."""
    from magi.runtime.llm.provider import ChatMessage
    from magi.runtime.agent import _build_messages_from_session

    state_dir = str(tmp_path / "state")
    (tmp_path / "state").mkdir()
    msgs = _build_messages_from_session(state_dir, "9001", None, "hi")
    assert len(msgs) == 1
    assert msgs[0].role == "user"
    assert msgs[0].content == "hi"


def test_build_messages_from_session_maps_system_to_user(
    monkeypatch, tmp_path,
):
    """A system message (the summary at messages[0] after
    compaction) is re-emitted as a ``user`` ChatMessage
    because Anthropic's wire Literal only allows
    ``user``/``assistant`` — the LLM treats a leading
    user message as prior context."""
    from magi.runtime.llm.provider import ChatMessage
    from magi.runtime.agent import _build_messages_from_session
    from magi.runtime.sessions import (
        SessionStore,
        SessionMessage,
    )

    state_dir = str(tmp_path / "state")
    (tmp_path / "state").mkdir()
    store = SessionStore(state_dir)
    sess = store.create("9001", employee_id=2)

    # Simulate a post-compaction session: summary at
    # index 0, two recent originals after.
    from magi.runtime.sessions import session_to_dict, session_from_dict

    sess.messages = [
        SessionMessage(role="system", text="[summary] old chat was about X",
                       ts="t", message_id="s1"),
        SessionMessage(role="user", text="recent 1",
                       ts="t", message_id="m1"),
        SessionMessage(role="assistant", text="recent 1 reply",
                       ts="t", message_id="m2"),
    ]
    sess.archive = [
        SessionMessage(role="user", text="ARCHIVED old",
                       ts="t", message_id="a1"),
    ]
    store._write(sess)

    msgs = _build_messages_from_session(state_dir, "9001", sess.session_id, "new")

    # 3 messages from session + 1 new = 4 total
    assert len(msgs) == 4
    # The system summary is mapped to a user message.
    assert msgs[0].role == "user"
    assert msgs[0].content.startswith("[summary]")
    # Recent originals kept their role.
    assert msgs[1].role == "user"
    assert msgs[1].content == "recent 1"
    assert msgs[2].role == "assistant"
    assert msgs[2].content == "recent 1 reply"
    # New user message appended last.
    assert msgs[3].role == "user"
    assert msgs[3].content == "new"


def test_build_messages_from_session_does_not_load_archive(
    monkeypatch, tmp_path,
):
    """The archive list is NOT loaded — only the active
    ``messages`` list. Operators view archive via
    ``GET /api/chat/sessions/{id}``."""
    from magi.runtime.agent import _build_messages_from_session
    from magi.runtime.sessions import (
        SessionStore,
        SessionMessage,
    )

    state_dir = str(tmp_path / "state")
    (tmp_path / "state").mkdir()
    store = SessionStore(state_dir)
    sess = store.create("9001", employee_id=2)
    sess.messages = [
        SessionMessage(role="user", text="only-active",
                       ts="t", message_id="m1"),
    ]
    sess.archive = [
        SessionMessage(role="user", text="ARCHIVED 1",
                       ts="t", message_id="a1"),
        SessionMessage(role="assistant", text="ARCHIVED 2",
                       ts="t", message_id="a2"),
    ]
    store._write(sess)

    msgs = _build_messages_from_session(state_dir, "9001", sess.session_id, "new")
    # 1 active + 1 new = 2, NOT 4 (archive excluded).
    assert len(msgs) == 2
    joined = " ".join(m.content for m in msgs)
    assert "ARCHIVED" not in joined
    assert "only-active" in joined
    assert "new" in joined


def test_build_messages_from_session_handles_legacy_file(
    monkeypatch, tmp_path,
):
    """A pre-D.17 session file (no archive field) loads
    with default archive and the messages load as-is."""
    from magi.runtime.agent import _build_messages_from_session
    from magi.runtime.sessions import (
        SessionStore,
        SessionMessage,
    )

    state_dir = str(tmp_path / "state")
    (tmp_path / "state").mkdir()
    store = SessionStore(state_dir)
    sess = store.create("9001", employee_id=2)
    sess.messages = [
        SessionMessage(role="user", text="legacy msg",
                       ts="t", message_id="m1"),
    ]
    # Bypass _write (which would emit archive=[]) and write
    # a raw old-format file to simulate a file written
    # before D.17.
    import json as _json
    from magi.runtime.sessions import session_path
    path = session_path(store._workspace, sess.chat_id, sess.session_id)
    path.write_text(_json.dumps({
        "schema_version": 1,
        "session_id": sess.session_id,
        "chat_id": sess.chat_id,
        "employee_id": sess.employee_id,
        "channel": sess.channel,
        "created_at": sess.created_at,
        "updated_at": sess.updated_at,
        "title": None,
        "messages": [
            {"role": "user", "text": "legacy msg",
             "ts": "t", "message_id": "m1"},
        ],
        # No archive / active_tail_count / last_compaction_at
    }), encoding="utf-8")

    msgs = _build_messages_from_session(state_dir, "9001", sess.session_id, "new")
    assert len(msgs) == 2
    assert msgs[0].role == "user"
    assert msgs[0].content == "legacy msg"
    assert msgs[1].content == "new"


# -- maybe_compact decision (no live provider) -----------------------------


@pytest.mark.asyncio
async def test_maybe_compact_noop_when_under_threshold(
    monkeypatch, tmp_path,
):
    """A short message list is well under the threshold
    → ``_maybe_compact`` returns immediately without
    touching the list or calling any LLM."""
    from magi.runtime.agent import _maybe_compact
    from magi.runtime.llm.provider import ChatMessage
    from magi.runtime.sessions import (
        SessionStore,
        SessionMessage,
    )
    from magi.runtime.state.settings import state_set

    state_dir = str(tmp_path / "state")
    (tmp_path / "state").mkdir()
    # The settings table is created by init_sqlite; without
    # it, ``state_set`` raises ``no such table: settings``.
    from magi.runtime.state import init_sqlite
    init_sqlite(state_dir)
    state_set(state_dir, "system.compact_context_window", "100000")
    state_set(state_dir, "system.compact_threshold_pct", "80")
    state_set(state_dir, "system.compact_keep_recent", "20")

    # Create a session with 3 short messages — well under
    # the threshold of 80K tokens.
    store = SessionStore(state_dir)
    sess = store.create("9001", employee_id=2)
    for i in range(3):
        sess.messages.append(SessionMessage(
            role="user", text=f"msg {i}",
            ts="t", message_id=f"m{i}",
        ))
    store._write(sess)

    # Build the same in-memory list the agent would.
    msgs = [ChatMessage(role=m.role, content=m.text) for m in sess.messages]
    msgs.append(ChatMessage(role="user", content="new"))

    await _maybe_compact(
        state_dir, "9001", sess.session_id, msgs,
        employee_provider="", employee_api_key="", employee_model=None,
    )

    # List untouched (3 prior + 1 new = 4).
    assert len(msgs) == 4
    # Session file untouched.
    sess2 = store.get("9001", sess.session_id)
    assert len(sess2.archive) == 0
    assert sess2.last_compaction_at is None


@pytest.mark.asyncio
async def test_maybe_compact_noop_when_message_count_below_keep_recent(
    monkeypatch, tmp_path,
):
    """Even with a tiny threshold, if the message count
    is below ``keep_recent`` there's nothing to compress
    (no old messages to archive)."""
    from magi.runtime.agent import _maybe_compact
    from magi.runtime.llm.provider import ChatMessage
    from magi.runtime.sessions import (
        SessionStore,
        SessionMessage,
    )
    from magi.runtime.state.settings import state_set

    state_dir = str(tmp_path / "state")
    (tmp_path / "state").mkdir()
    from magi.runtime.state import init_sqlite
    init_sqlite(state_dir)
    state_set(state_dir, "system.compact_context_window", "100")
    state_set(state_dir, "system.compact_threshold_pct", "1")
    state_set(state_dir, "system.compact_keep_recent", "20")

    store = SessionStore(state_dir)
    sess = store.create("9001", employee_id=2)
    sess.messages = [
        SessionMessage(role="user", text="only msg",
                       ts="t", message_id="m1"),
    ]
    store._write(sess)
    msgs = [ChatMessage(role="user", content="only msg"),
            ChatMessage(role="user", content="new")]

    await _maybe_compact(
        state_dir, "9001", sess.session_id, msgs,
        employee_provider="", employee_api_key="", employee_model=None,
    )

    # No archive written; in-memory list untouched.
    assert len(msgs) == 2
    sess2 = store.get("9001", sess.session_id)
    assert len(sess2.archive) == 0


@pytest.mark.asyncio
async def test_maybe_compact_noop_when_no_session_id(
    monkeypatch, tmp_path,
):
    """The first turn of a conversation has no session
    yet. ``_maybe_compact`` is a no-op even when the
    message list (single user msg) is well under the
    threshold."""
    from magi.runtime.agent import _maybe_compact
    from magi.runtime.llm.provider import ChatMessage

    state_dir = str(tmp_path / "state")
    (tmp_path / "state").mkdir()

    msgs = [ChatMessage(role="user", content="first turn")]
    await _maybe_compact(
        state_dir, "9001", None, msgs,
        employee_provider="", employee_api_key="", employee_model=None,
    )
    assert len(msgs) == 1