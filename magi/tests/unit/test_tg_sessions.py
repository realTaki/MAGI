"""In-process tests for the TG inbound session-resolution path.

D.18 moved sessions from per-file JSON to SQLite (``chat_sessions`` +
``chat_messages``). The TG handler logic — "one session per
TG tgid forever, mint a fresh one if the prior was
deleted" — stays the same; only the assertions move from
``glob("*.json")`` to ORM queries.

The test surface below exercises :func:`_resolve_or_create_tg_session`
(the helper TG inbound calls before appending the user message)
plus the post-append state for the resulting session.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi.agent.memory.session import SessionStore
from magi.agent.db import init_sqlite
from magi.agent.db import ChatSession, init_orm, open_session


# ────────────────────────────────────────────────────────────────── #
# fixtures
# ────────────────────────────────────────────────────────────────── #


@pytest.fixture
def tg_session_env(monkeypatch, tmp_path):
    """Per-test isolated state dir + fresh ORM engine.

    ``MAGI_STATE_DIR`` points at ``<tmp_path>/memories``
    (matches the in-container layout) and the ORM engine
    is reset so each test gets a fresh DB.
    """
    state = tmp_path / "memories"
    state.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))

    import magi.agent.db.engine as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None

    init_sqlite(str(state))
    init_orm(str(state))
    return state, tmp_path


def _row_for(tgid: str):
    """Fetch the session row for ``tgid`` (helper for assertions)."""
    with open_session() as db:
        return db.query(ChatSession).filter_by(tgid=tgid).first()


# ────────────────────────────────────────────────────────────────── #
# happy path
# ────────────────────────────────────────────────────────────────── #


def test_first_call_creates_session(tg_session_env):
    """No prior session on disk → helper mints a fresh one."""
    from magi.channels.telegram.bot import _resolve_or_create_tg_session

    state_dir, _workspace = tg_session_env
    store = SessionStore(state_dir)

    sid = _resolve_or_create_tg_session(store, "6240201712", uid=42)

    # ULID-shaped: 26 Crockford base32 chars.
    assert isinstance(sid, str)
    assert len(sid) == 26

    # Row landed in SQLite and carries channel="tg" so a
    # future replay tool can tell which surface produced
    # each session.
    row = _row_for("6240201712")
    assert row is not None
    assert row.session_id == sid
    assert row.channel == "tg"
    assert row.tgid == "6240201712"
    assert row.uid == 42


def test_second_call_reuses_same_session(tg_session_env):
    """Subsequent messages in the same TG chat append to
    the existing row rather than spawning a new thread.

    This is the core "one session per tgid forever" policy.
    """
    from magi.channels.telegram.bot import _resolve_or_create_tg_session

    state_dir, _workspace = tg_session_env
    store = SessionStore(state_dir)

    sid1 = _resolve_or_create_tg_session(store, "6240201712", uid=42)
    sid2 = _resolve_or_create_tg_session(store, "6240201712", uid=42)
    sid3 = _resolve_or_create_tg_session(store, "6240201712", uid=42)

    assert sid1 == sid2 == sid3, "TG must reuse one session per tgid"

    # Still only one row.
    with open_session() as db:
        count = db.query(ChatSession).filter_by(tgid="6240201712").count()
    assert count == 1


# ────────────────────────────────────────────────────────────────── #
# resilience
# ────────────────────────────────────────────────────────────────── #


def test_call_after_session_deleted_mints_fresh(tg_session_env):
    """If the operator wiped the session (manual ``DELETE FROM``,
    a future "clear history" affordance, etc.), the next
    inbound message creates a brand-new session rather than
    crashing.

    Pre-D.18 the wipe was a ``rm`` of the JSON file; D.18
    it's a ``DELETE`` row operation. Both should leave the
    helper minting a fresh id on the next call.
    """
    from magi.channels.telegram.bot import _resolve_or_create_tg_session

    state_dir, _workspace = tg_session_env
    store = SessionStore(state_dir)

    sid1 = _resolve_or_create_tg_session(store, "6240201712", uid=42)
    # Operator wipes the session.
    store.delete(42, sid1)
    with open_session() as db:
        assert db.query(ChatSession).filter_by(tgid="6240201712").count() == 0

    sid2 = _resolve_or_create_tg_session(store, "6240201712", uid=42)
    assert sid1 != sid2, "after wipe, helper should mint a fresh id"


def test_different_tgids_get_different_sessions(tg_session_env):
    """Two employees chatting this EVE get two distinct rows
    — DB-level ``tgid`` scoping mirrors the WebUI guarantee
    so one user's history never bleeds into another's.
    """
    from magi.channels.telegram.bot import _resolve_or_create_tg_session

    state_dir, _workspace = tg_session_env
    store = SessionStore(state_dir)

    sid_a = _resolve_or_create_tg_session(store, "6240201712", uid=1)
    sid_b = _resolve_or_create_tg_session(store, "9876543210", uid=2)

    assert sid_a != sid_b

    # And each tgid's row carries its own tgid and
    # uid (the operator identity, not the chat's).
    row_a = _row_for("6240201712")
    row_b = _row_for("9876543210")
    assert row_a is not None and row_a.uid == 1
    assert row_b is not None and row_b.uid == 2
    assert row_a.session_id == sid_a
    assert row_b.session_id == sid_b


def test_messages_persist_to_session(tg_session_env):
    """End-to-end: helper creates a session, append_messages
    persists the inbound + outbound rows, get sees them."""
    from magi.channels.telegram.bot import _resolve_or_create_tg_session
    from magi.agent.memory.session import SessionMessage, new_session_id

    state_dir, _workspace = tg_session_env
    store = SessionStore(state_dir)

    sid = _resolve_or_create_tg_session(store, "6240201712", uid=42)
    # D.23: store key is uid (int) — match what
    # the helper used to mint the row.
    store.append_messages(42, sid, [
        SessionMessage(
            role="user", text="hi",
            ts="2026-07-03T10:00:00Z",
            message_id=new_session_id(),
        ),
    ])
    store.append_messages(42, sid, [
        SessionMessage(
            role="assistant", text="hello!",
            ts="2026-07-03T10:00:05Z",
            message_id=new_session_id(),
        ),
    ])

    fetched = store.get(42, sid)
    assert fetched is not None
    roles = [m.role for m in fetched.messages]
    assert "user" in roles
    assert "assistant" in roles
    # Channel flag round-trips.
    assert fetched.channel == "tg"
    assert fetched.tgid == "6240201712"
    assert fetched.uid == 42


# ────────────────────────────────────────────────────────────────── #
# D.10 cross-channel continuity (regression)
# ────────────────────────────────────────────────────────────────── #
#
# Pre-fix the helper used ``list_summaries`` with no channel
# filter, then re-checked ``channel == 'tg'`` in Python. When
# the most recent any-channel row belonged to WebUI (the
# same employee owns sessions across channels since D.23),
# the helper would mint a fresh TG session every time. In
# real usage the operator alternating TG ↔ WebUI fragmented
# the TG history into N rows, contradicting the "one TG
# session per tgid forever" promise in the helper
# docstring.
#
# The fix is ``find_latest_tg_session`` (a SQL-side
# ``WHERE channel = 'tg'`` filter), so this test pins that
# the latest TG row is reused across channel boundaries.


def test_tg_session_reused_after_webui_message_in_between(
    tg_session_env,
):
    """Operator sends a TG message, then a WebUI message,
    then another TG message. The second TG inbound must
    land on the same TG session as the first — the WebUI
    message in between must NOT cause a new TG row to be
    minted.

    Three rounds:

      1. ``_resolve_or_create_tg_session`` → mint ``sid_a``.
      2. Operator opens WebUI on the same uid and
         creates a fresh webui session ``sid_webui`` with a
         newer ``updated_at`` than ``sid_a``.
      3. ``_resolve_or_create_tg_session`` again — must
         return ``sid_a``, NOT a fresh id.
    """
    from magi.channels.telegram.bot import _resolve_or_create_tg_session

    state_dir, _workspace = tg_session_env
    store = SessionStore(state_dir)

    # Round 1: first TG inbound.
    sid_a = _resolve_or_create_tg_session(store, "6240201712", uid=42)
    assert isinstance(sid_a, str)

    # Round 2: WebUI session for the same employee. Created
    # AFTER the TG row, so its ``updated_at`` is newer.
    # Without the channel filter, the buggy helper would
    # see this as "latest" and mint a fresh TG session.
    sid_webui = store.create(
        42, channel="webui",
    ).session_id

    # Round 3: another TG inbound — must reuse ``sid_a``.
    sid_a2 = _resolve_or_create_tg_session(store, "6240201712", uid=42)
    assert sid_a2 == sid_a, (
        "TG session must be reused even after a newer "
        "WebUI session exists; got a fresh id "
        f"({sid_a2!r}) instead of the original {sid_a!r}"
    )

    # Only one TG row exists.
    with open_session() as db:
        tg_count = (
            db.query(ChatSession)
            .filter_by(uid=42, channel="tg")
            .count()
        )
    assert tg_count == 1, (
        f"expected 1 TG session, found {tg_count} — the "
        "cross-channel continuity contract is broken"
    )

    # The webui row is still there, untouched.
    with open_session() as db:
        webui_row = db.get(ChatSession, sid_webui)
    assert webui_row is not None
    assert webui_row.channel == "webui"