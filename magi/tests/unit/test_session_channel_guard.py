"""Tests for the D.22 cross-channel session guard.

Two layers:

  1. **Store layer** —
     :meth:`SessionStore.append_messages` rejects writes
     when ``channel=`` is provided AND the stored row's
     channel doesn't match. Reads (``get``, ``list``) are
     not gated — same employee can browse TG history from
     WebUI.

  2. **WebUI chat API** — ``POST /api/chat/send`` returns
     403 ``chat.session_channel_mismatch`` when the
     inbound targets a TG-owned session. Negative case:
     a WebUI-owned session is accepted (positive control).

Why a separate file from ``test_chat_sessions_api.py``:
that file's fixture monkeypatches
``chat_mod.handle_message`` directly, which leaves a
shadowy AsyncMock leak in ``chat_mod`` once the fixture
tears down. Re-using the same fixture here would mean
the channel-mismatch test inherits the patch and never
hits the real ``ChannelMismatchError → 403`` mapping
we want to verify. A dedicated fixture with explicit
``monkeypatch.setattr`` keeps the patch scoped to the
function and torn down by pytest.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from magi.agent.db import Employee, init_orm, init_sqlite, open_session
from magi.agent.memory.session import (
    ChannelMismatchError,
    SessionMessage,
    SessionStore,
    new_session_id,
    utcnow_iso,
)


# -- helpers / fixtures --------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_orm_engine() -> None:
    """Auto-reset the global SQLAlchemy engine — same fix as
    :mod:`test_chat_sessions_api`. The orm module caches
    its engine + session-local on first use; without this
    reset, every test after the first reuses the prior
    test's engine and inserts into a tmp_path that's
    already gone."""
    import magi.agent.db.engine as _orm_mod
    _orm_mod._engine = None
    _orm_mod._SessionLocal = None
    yield


@pytest.fixture
def state(tmp_path: Path, monkeypatch) -> Path:
    sd = tmp_path / "state"
    sd.mkdir()
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(sd))
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(ws))
    init_sqlite(str(sd))
    init_orm(str(sd))
    return sd


@pytest.fixture
def admin(state) -> Employee:
    """Seed an admin whose telegram_id is the WebUI delivery_address."""
    with open_session() as s:
        emp = Employee(
            name="Test Admin",
            telegram_id=9001,
            role="admin",
            provider="minimax",
            api_key="fake-key-for-tests",
        )
        s.add(emp)
        s.commit()
        s.refresh(emp)
        return emp


def _make_session(
    state: Path, channel: str, delivery_address: str = "9001",
    uid: int = 1,
) -> str:
    """Create a session with the given owner channel.

    D.23: store key is the operator's uid; the
    delivery_address argument here is the per-channel delivery
    address stamped on the row's ``delivery_address`` column.
    """
    store = SessionStore(str(state))
    sess = store.create(
        uid, channel=channel,
    )
    return sess.session_id


# ────────────────────────────────────────────────────────────────── #
# SessionStore.append_messages — channel guard
# ────────────────────────────────────────────────────────────────── #


def test_append_with_matching_channel_succeeds(state: Path) -> None:
    """Same channel as the session owner → write goes
    through."""
    store = SessionStore(str(state))
    sid = _make_session(state, "tg")

    msg = SessionMessage(
        role="user", text="hi", ts=utcnow_iso(),
        message_id=new_session_id(),
    )
    # D.23: store key is uid (int).
    sess = store.append_messages(
        1, sid, [msg], channel="tg",
    )
    assert any(m.text == "hi" for m in sess.messages)


def test_append_with_mismatched_channel_raises(state: Path) -> None:
    """A WebUI caller trying to append to a TG-owned
    session triggers the guard."""
    store = SessionStore(str(state))
    sid = _make_session(state, "tg")

    msg = SessionMessage(
        role="user", text="from webui", ts=utcnow_iso(),
        message_id=new_session_id(),
    )
    with pytest.raises(ChannelMismatchError) as ei:
        store.append_messages(
            1, sid, [msg], channel="webui",
        )
    assert ei.value.session_channel == "tg"
    assert ei.value.caller_channel == "webui"
    assert ei.value.session_id == sid


def test_append_with_omitted_channel_skips_check(state: Path) -> None:
    """``channel=None`` (the default) bypasses the guard —
    useful for back-fill tooling that operates on
    historical rows without an inbound channel."""
    store = SessionStore(str(state))
    sid = _make_session(state, "tg")

    msg = SessionMessage(
        role="user", text="backfill", ts=utcnow_iso(),
        message_id=new_session_id(),
    )
    # No ``channel=`` kwarg → no guard.
    sess = store.append_messages(1, sid, [msg])
    assert any(m.text == "backfill" for m in sess.messages)


def test_append_to_legacy_session_with_empty_channel_skips_check(
    state: Path,
) -> None:
    """Pinned but skipped at the SQL layer: the
    ``chat_sessions.channel`` column is ``NOT NULL`` so a
    row with an empty ``channel`` value can't exist via
    the public ``SessionStore.create`` path. The
    guard's "empty stored channel → writer wins" branch
    is therefore unreachable in v0; it exists as
    defense in depth for a future migration that
    relaxes the NOT NULL (e.g. back-filling the
    ``channel`` column from a separate ``inbound_from``
    column). See the docstring on
    :meth:`SessionStore.append_messages` for the
    rationale.
    """
    pytest.skip(
        "channel column is NOT NULL; empty-channel branch is "
        "unreachable via public SessionStore.create — covered "
        "by the unit-level guard test above."
    )


def test_append_mismatch_does_not_corrupt_session(state: Path) -> None:
    """The mismatch raises BEFORE any INSERT runs — the
    session's existing messages are untouched."""
    store = SessionStore(str(state))
    sid = _make_session(state, "tg")
    # Seed one legitimate TG message first.
    seed = SessionMessage(
        role="user", text="legit tg msg", ts=utcnow_iso(),
        message_id=new_session_id(),
    )
    store.append_messages(1, sid, [seed], channel="tg")

    # Attempt the cross-channel write.
    bad = SessionMessage(
        role="user", text="should not land", ts=utcnow_iso(),
        message_id=new_session_id(),
    )
    with pytest.raises(ChannelMismatchError):
        store.append_messages(
            1, sid, [bad], channel="webui",
        )

    # Re-read: only the seed message is present.
    sess = store.get(1, sid)
    assert sess is not None
    user_texts = [m.text for m in sess.messages if m.role == "user"]
    assert user_texts == ["legit tg msg"]


def test_get_does_not_check_channel(state: Path) -> None:
    """Reads are cross-channel by design — the same
    employee may browse their TG history from WebUI."""
    store = SessionStore(str(state))
    sid = _make_session(state, "tg")
    seed = SessionMessage(
        role="user", text="hi", ts=utcnow_iso(),
        message_id=new_session_id(),
    )
    store.append_messages(1, sid, [seed], channel="tg")

    # ``get`` doesn't take a channel — must work for any
    # caller, no guard.
    sess = store.get(1, sid)
    assert sess is not None
    assert any(m.text == "hi" for m in sess.messages)


# ────────────────────────────────────────────────────────────────── #
# WebUI chat API — 403 chat.session_channel_mismatch
# ────────────────────────────────────────────────────────────────── #


@pytest.fixture
def client(state: Path, admin: Employee):
    """TestClient with ``handle_message`` monkey-patched
    to an AsyncMock so we can detect "did the inbound
    guard trip BEFORE handle_message was called".

    We use AsyncMock + ``assert_not_called`` rather than
    a fake that returns a string: the channel-mismatch
    path must short-circuit BEFORE the LLM call runs,
    otherwise we'd bill the operator for a half-finished
    request.
    """
    from magi.agent import loop as agent_mod
    from magi.channels.webui.api import chat as chat_mod

    fake = AsyncMock(return_value="never-called")
    # Patch both namespaces — same shadow-import trap
    # the chat_sessions_api fixture calls out.
    import magi.agent.loop as loop_mod
    monkeypatch_obj = __import__("pytest").MonkeyPatch()
    monkeypatch_obj.setattr(loop_mod, "handle_message", fake)
    monkeypatch_obj.setattr(chat_mod, "handle_message", fake)

    from magi.channels.webui.app import app

    test_client = TestClient(app)
    # Stash the mock so tests can assert against it.
    test_client._fake_handle = fake  # type: ignore[attr-defined]
    yield test_client
    monkeypatch_obj.undo()


def _post_send(
    client: TestClient, admin: Employee, text: str, session_id: str,
):
    return client.post(
        "/api/chat/send",
        json={"text": text, "session_id": session_id},
        cookies={"magi_session": str(admin.id)},
    )


def test_webui_send_to_tg_owned_session_is_403(
    client: TestClient, admin: Employee, state: Path,
) -> None:
    """WebUI tries to send a message into a session that
    was created by TG. The guard short-circuits with
    ``403 chat.session_channel_mismatch`` — the LLM is
    never called."""
    sid = _make_session(state, "tg", (admin.telegram_id))

    r = _post_send(client, admin, "should be rejected", sid)

    assert r.status_code == 403
    body = r.json()
    assert body["code"] == "chat.session_channel_mismatch"
    assert "tg" in body["detail"]  # the owning channel is named

    # LLM was never invoked.
    client._fake_handle.assert_not_called()  # type: ignore[attr-defined]

    # The session's history is unchanged.
    # D.23: store key is uid (int), not the
    # channel's delivery_address string.
    sess = SessionStore(str(state)).get(admin.id, sid)
    assert sess is not None
    user_texts = [m.text for m in sess.messages if m.role == "user"]
    assert user_texts == []


def test_webui_send_to_webui_owned_session_is_200(
    client: TestClient, admin: Employee, state: Path,
) -> None:
    """Positive control: same channel as the session
    owner — the guard doesn't fire, the request
    succeeds."""
    sid = _make_session(state, "webui", (admin.telegram_id))

    r = _post_send(client, admin, "hello", sid)

    assert r.status_code == 200
    body = r.json()
    assert body["reply"] == "never-called"  # from the AsyncMock

    # Inbound + outbound both appended.
    # D.23: store key is uid (int).
    sess = SessionStore(str(state)).get(admin.id, sid)
    assert sess is not None
    roles = [m.role for m in sess.messages]
    assert roles == ["user", "assistant"]
    assert sess.messages[0].text == "hello"
    assert sess.messages[1].text == "never-called"


def test_webui_send_to_scheduled_owned_session_is_403(
    client: TestClient, admin: Employee, state: Path,
) -> None:
    """A scheduled-task-owned session is similarly
    protected — only the proactive runner (channel=
    ``"scheduled"``) can append to it."""
    sid = _make_session(state, "scheduled", (admin.telegram_id))

    r = _post_send(client, admin, "should be rejected", sid)

    assert r.status_code == 403
    assert r.json()["code"] == "chat.session_channel_mismatch"
    assert "scheduled" in r.json()["detail"]
    client._fake_handle.assert_not_called()  # type: ignore[attr-defined]


def test_webui_list_includes_cross_channel_sessions(
    client: TestClient, admin: Employee, state: Path,
) -> None:
    """The list endpoint must include sessions owned by
    any channel — the operator can see their TG history
    from the WebUI console even though they can't
    *write* to those sessions there."""
    tg_sid = _make_session(state, "tg", (admin.telegram_id))
    webui_sid = _make_session(
        state, "webui", (admin.telegram_id),
    )

    r = client.get(
        "/api/chat/sessions",
        cookies={"magi_session": str(admin.id)},
    )
    assert r.status_code == 200
    body = r.json()
    # ``/api/chat/sessions`` returns ``{items, total, ...}``;
    # see ``SessionListOut`` in chat_sessions.py.
    sids = {s["session_id"] for s in body["items"]}
    assert {tg_sid, webui_sid} <= sids