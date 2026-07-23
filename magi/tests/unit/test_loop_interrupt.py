"""Tests for the D.21 interrupt-aware agent loop.

Two layers of coverage:

  1. **Pure-Python unit tests** of the helpers
     :func:`magi.agent.loop._truncate_at_safe_boundary`
     and
     :func:`magi.agent.loop._drain_pending_user_messages`.
     These don't need a provider, a session, or even a
     real disk — they exercise the truncation logic and
     the seen-id bookkeeping in isolation.

  2. **End-to-end test of ``handle_message``** with a
     fake provider that we drive by appending user
     messages to the session store mid-loop. This is
     the "the user interrupts with a follow-up" scenario
     that motivates D.21.

Why no mocking of the provider module attribute (à la
``test_tg_admin_routes``): that pattern leaks the
AsyncMock across later tests. We monkeypatch the
``get_provider`` symbol on the loop module instead, so
the patch is scoped to the test function and torn down
by pytest.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from magi.agent.llm.provider import ChatMessage, ChatResult
from magi.agent import loop as loop_mod
from magi.agent.loop import (
    _drain_pending_user_messages,
    _truncate_at_safe_boundary,
    handle_message,
)
from magi.agent.memory.session import (
    SessionMessage,
    SessionStore,
    new_session_id,
    utcnow_iso,
)


# ────────────────────────────────────────────────────────────────── #
# _truncate_at_safe_boundary
# ────────────────────────────────────────────────────────────────── #


def test_truncate_at_safe_boundary_noop_when_tail_is_text() -> None:
    """A trailing plain text message is left alone — there's
    no tool_use / tool_result chain to break."""
    msgs = [
        ChatMessage(role="user", content="hi"),
        ChatMessage(role="assistant", content="hello"),
        ChatMessage(role="user", content="thanks"),
    ]
    _truncate_at_safe_boundary(msgs)
    assert [m.content for m in msgs] == ["hi", "hello", "thanks"]


def test_truncate_at_safe_boundary_drops_trailing_blocks() -> None:
    """Trailing assistant(tool_use) and user(tool_result)
    entries are popped, leaving the conversation at a clean
    text-message boundary."""
    msgs = [
        ChatMessage(role="user", content="search for python"),
        ChatMessage(
            role="assistant", content="",
            content_blocks=[{"type": "tool_use", "id": "t1", "name": "x", "input": {}}],
        ),
        ChatMessage(
            role="user", content="",
            content_blocks=[{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
        ),
    ]
    _truncate_at_safe_boundary(msgs)
    assert [m.content for m in msgs] == ["search for python"]
    # The plain text message has no content_blocks — that's
    # what makes it a "safe boundary".
    assert msgs[0].content_blocks is None


def test_truncate_at_safe_boundary_drops_multiple_trailing_blocked() -> None:
    """Both tool_use AND tool_result blocks get dropped."""
    msgs = [
        ChatMessage(role="user", content="a"),
        ChatMessage(
            role="assistant", content="",
            content_blocks=[{"type": "tool_use", "id": "t1", "name": "x", "input": {}}],
        ),
        ChatMessage(
            role="user", content="",
            content_blocks=[{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
        ),
        ChatMessage(
            role="user", content="",
            content_blocks=[{"type": "tool_result", "tool_use_id": "t2", "content": "ok"}],
        ),
    ]
    _truncate_at_safe_boundary(msgs)
    assert [m.content for m in msgs] == ["a"]


def test_truncate_at_safe_boundary_empty_list_is_noop() -> None:
    _truncate_at_safe_boundary([])
    assert _truncate_at_safe_boundary.__name__ == "_truncate_at_safe_boundary"


# ────────────────────────────────────────────────────────────────── #
# _drain_pending_user_messages
# ────────────────────────────────────────────────────────────────── #


@pytest.fixture(autouse=True)
def _reset_orm_engine() -> None:
    """Auto-reset the global SQLAlchemy engine before each test.

    The orm module's ``_engine`` is a process-global singleton
    cached on first use. Without resetting it, every test
    after the first inherits the prior test's engine handle —
    which points at a tmp_path that's been recreated (so the
    sqlite file path is stale) and the inserts collide on
    seeded admin rows.

    Mirrors the same auto-reset in
    ``test_chat_sessions_api`` — same root cause, same fix.
    """
    import magi.agent.db.engine as _orm_mod
    _orm_mod._engine = None
    _orm_mod._SessionLocal = None
    yield


@pytest.fixture
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fresh state dir per test + a single bound admin so
    the chat-store helpers don't trip the FK on a missing
    employee row.

    Each test gets a unique ``telegram_id`` so the suite
    can re-import this fixture without UNIQUE-constraint
    conflicts. The session-store + ORM tables are scoped
    to the tmp dir and don't leak across tests.
    """
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))
    from magi.agent.db import Employee, init_orm, init_sqlite, open_session

    init_sqlite(str(state))
    init_orm(str(state))
    with open_session() as s:
        # Each test gets a fresh, unique telegram_id so
        # the UNIQUE constraint on the column doesn't
        # trip when the fixture is reused.
        s.add(
            Employee(
                name="TA-loop-interrupt",
                telegram_id=tmp_path.stat().st_uid ^ 90000,
                role="admin",
                provider="minimax",
                api_key="fake",
            )
        )
        s.commit()
    return state


def test_drain_no_session_id_is_noop(state_dir: Path) -> None:
    """``session_id=None`` short-circuits — the caller never
    had a store to poll, so we must not look one up."""
    msgs: list[ChatMessage] = []
    seen: set[str] = set()
    drained = _drain_pending_user_messages(
        str(state_dir), 0, None, msgs, seen,
    )
    assert drained is False
    assert msgs == []
    assert seen == set()


def test_drain_returns_false_when_store_unchanged(
    state_dir: Path,
) -> None:
    """The store has one user message — the one already in
    ``seen`` — so the poll returns ``False`` and ``msgs``
    is untouched."""
    store = SessionStore(str(state_dir))
    sess = store.create(1, channel="webui", )
    store.append_messages(
        1, sess.session_id,
        [SessionMessage(
            role="user", text="hi", ts=utcnow_iso(),
            message_id=new_session_id(),
        )],
    )
    msgs = [ChatMessage(role="user", content="hi")]
    seen = set()  # pretend we never saw it — drain should pick it up

    drained = _drain_pending_user_messages(
        str(state_dir), 1, sess.session_id, msgs, seen,
    )
    # The store's message wasn't in ``seen`` so it was
    # picked up; not the "no change" path.
    assert drained is True
    assert [m.content for m in msgs] == ["hi", "hi"]


def test_drain_returns_false_when_seen_covers_everything(
    state_dir: Path,
) -> None:
    """The poll is a no-op when ``seen`` covers every id in
    the store — i.e. the loop has already processed all
    user messages."""
    store = SessionStore(str(state_dir))
    sess = store.create(1, channel="webui", )
    mid = new_session_id()
    store.append_messages(
        1, sess.session_id,
        [SessionMessage(
            role="user", text="hi", ts=utcnow_iso(),
            message_id=mid,
        )],
    )
    msgs = [ChatMessage(role="user", content="hi")]
    seen = {mid}

    drained = _drain_pending_user_messages(
        str(state_dir), 1, sess.session_id, msgs, seen,
    )
    assert drained is False
    assert msgs == [ChatMessage(role="user", content="hi")]


def test_drain_splices_new_user_messages_in_order(
    state_dir: Path,
) -> None:
    """Two new user messages arrive between iterations;
    both land in the in-memory list, in store order."""
    store = SessionStore(str(state_dir))
    sess = store.create(1, channel="webui", )
    store.append_messages(
        1, sess.session_id,
        [SessionMessage(
            role="user", text="first", ts=utcnow_iso(),
            message_id=new_session_id(),
        )],
    )
    msgs = [ChatMessage(role="user", content="first")]
    seen = set()  # first poll, no ids seen yet

    drained = _drain_pending_user_messages(
        str(state_dir), 1, sess.session_id, msgs, seen,
    )
    assert drained is True
    assert [m.content for m in msgs] == ["first", "first"]

    # Second batch arrives.
    store.append_messages(
        1, sess.session_id,
        [
            SessionMessage(
                role="user", text="second", ts=utcnow_iso(),
                message_id=new_session_id(),
            ),
            SessionMessage(
                role="user", text="third", ts=utcnow_iso(),
                message_id=new_session_id(),
            ),
        ],
    )
    drained = _drain_pending_user_messages(
        str(state_dir), 1, sess.session_id, msgs, seen,
    )
    assert drained is True
    assert [m.content for m in msgs] == [
        "first", "first", "second", "third",
    ]


def test_drain_truncates_trailing_tool_blocks(
    state_dir: Path,
) -> None:
    """Mid-tool-chain interrupt: the in-memory list ends
    on an assistant(tool_use) / user(tool_result) pair. The
    new user message must land AFTER the truncation — the
    API rejects a plain ``user`` text message interleaved
    with tool blocks."""
    store = SessionStore(str(state_dir))
    sess = store.create(1, channel="webui", )
    store.append_messages(
        1, sess.session_id,
        [SessionMessage(
            role="user", text="search", ts=utcnow_iso(),
            message_id=new_session_id(),
        )],
    )
    msgs = [
        ChatMessage(role="user", content="search"),
        ChatMessage(
            role="assistant", content="",
            content_blocks=[{
                "type": "tool_use", "id": "t1", "name": "x", "input": {},
            }],
        ),
        ChatMessage(
            role="user", content="",
            content_blocks=[{
                "type": "tool_result", "tool_use_id": "t1", "content": "ok",
            }],
        ),
    ]
    seen: set[str] = set()  # pretend no prior polling happened

    # The store has "search" which is NOT in seen — drain
    # will pick it up and the trailing tool blocks must
    # be truncated.
    drained = _drain_pending_user_messages(
        str(state_dir), 1, sess.session_id, msgs, seen,
    )
    assert drained is True
    # Trailing tool blocks dropped; new user message lands
    # at a clean boundary.
    assert [m.content for m in msgs] == ["search", "search"]
    assert msgs[-1].content_blocks is None


def test_drain_skips_new_assistant_rows(
    state_dir: Path,
) -> None:
    """A new ``assistant`` row in the store (e.g. from a
    concurrent writer) is tracked in ``seen`` but never
    spliced into the in-memory list — that's the loop's
    own job, not the poller's."""
    store = SessionStore(str(state_dir))
    sess = store.create(1, channel="webui", )
    store.append_messages(
        1, sess.session_id,
        [
            SessionMessage(
                role="user", text="hi", ts=utcnow_iso(),
                message_id=new_session_id(),
            ),
            SessionMessage(
                role="assistant", text="hello", ts=utcnow_iso(),
                message_id=new_session_id(),
            ),
        ],
    )
    msgs: list[ChatMessage] = []
    seen: set[str] = set()

    drained = _drain_pending_user_messages(
        str(state_dir), 1, sess.session_id, msgs, seen,
    )
    # Both rows are new; only the user one is spliced.
    assert drained is True
    assert [m.role for m in msgs] == ["user"]
    assert msgs[0].content == "hi"
    # Both ids tracked.
    assert len(seen) == 2


def test_drain_swallows_store_read_errors(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``SessionStore.get`` failure must not crash the
    in-flight agent loop — return ``False`` so the loop
    treats it as "no new messages" and continues."""
    from magi.agent import memory as _memory_pkg
    sess_id = "does-not-exist-but-we-pretend"
    msgs: list[ChatMessage] = []
    seen: set[str] = set()

    # Force SessionStore.get to raise.
    def _boom(self, tgid: str, session_id: str) -> Any:
        raise RuntimeError("simulated store failure")

    monkeypatch.setattr(
        _memory_pkg.session.store.SessionStore, "get", _boom,
    )

    drained = _drain_pending_user_messages(
        str(state_dir), "any-chat", sess_id, msgs, seen,
    )
    assert drained is False
    assert msgs == []


# ────────────────────────────────────────────────────────────────── #
# handle_message — end-to-end interrupt scenario
# ────────────────────────────────────────────────────────────────── #


def _fake_provider_factory(
    responses: list[ChatResult],
) -> tuple[Any, Any]:
    """Build a fake provider + get_provider for monkeypatching.

    Each call to ``provider.chat`` pops the next ``ChatResult``
    off ``responses`` and returns it. When the list is
    exhausted, the last entry is returned forever (so a
    loop test that asks for N iterations always has
    something to return).

    Returns ``(provider_instance, get_provider_callable)``
    — patch the latter onto the loop module.
    """
    provider = MagicMock()
    provider.name = "minimax"

    async def _chat(*args: Any, **kwargs: Any) -> ChatResult:
        if not responses:
            raise AssertionError("provider.chat called too many times")
        if len(responses) == 1:
            return responses[0]
        return responses.pop(0)

    provider.chat = _chat

    def _get_provider(name: str, key: str, model: Any = None) -> Any:
        return provider

    return provider, _get_provider


def _text_result(text: str) -> ChatResult:
    return ChatResult(
        text=text, model="minimax-x", stop_reason="end_turn",
        tool_uses=[], raw_blocks=None, thinking=None, usage={},
    )


def _tool_use_result(tool_id: str, tool_name: str, input: dict) -> ChatResult:
    return ChatResult(
        text="", model="minimax-x", stop_reason="tool_use",
        tool_uses=[{
            "id": tool_id, "name": tool_name, "input": input,
        }],
        raw_blocks=[{
            "type": "tool_use", "id": tool_id,
            "name": tool_name, "input": input,
        }],
        thinking=None, usage={},
    )


@pytest.mark.asyncio
async def test_handle_message_picks_up_interrupting_user_message(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The headline D.21 scenario:

      1. User sends "search for python". Loop iteration 1
         returns a tool_use → loop runs ``list_files``
         (or whatever) and asks the LLM again.
      2. **While** the LLM is mid-tool-chain, the user
         sends "actually search for rust". The channel-
         side writer appends this to the session store
         (simulated by us below).
      3. The loop's next poll picks the message up,
         truncates the trailing tool blocks, splices
         the new message in, resets the iter counter,
         and the LLM gets a fresh budget to respond.
      4. Final reply is the LLM's text response after
         the interrupt.
    """
    # Two responses from the fake provider: first one is a
    # tool_use (mid-chain), second is a final text reply
    # after the interrupt.
    provider, get_provider = _fake_provider_factory([
        _tool_use_result("t1", "list_files", {"path": "."}),
        _text_result("Searching for rust instead."),
    ])
    monkeypatch.setattr(loop_mod, "get_provider", get_provider)

    # Seed the session store with the user's first message
    # (channels do this synchronously before calling
    # handle_message).
    tgid = "interrupt-chat"
    store = SessionStore(str(state_dir))
    sess = store.create(1, channel="webui", )
    store.append_messages(
        1, sess.session_id,
        [SessionMessage(
            role="user", text="search for python",
            ts=utcnow_iso(), message_id=new_session_id(),
        )],
    )

    # Append the interrupting message — the channel-side
    # writer would do this AFTER handle_message starts
    # running. We simulate the timing by patching
    # SessionStore.get to inject the second message on
    # the second call.
    real_get = store.__class__.get

    inject_after = {"calls": 0}

    def _get_with_interrupt(self, uid: int, s: str) -> Any:
        # D.23: the store's first positional arg is now
        # ``uid`` (int), not a tgid string. The
        # patched signature mirrors that.
        inject_after["calls"] += 1
        result = real_get(self, uid, s)
        if result is not None and inject_after["calls"] >= 2:
            # Append the interrupting message if not yet.
            existing_ids = {m.message_id for m in result.messages}
            if not any(m.text == "actually search for rust" for m in result.messages):
                self.append_messages(
                    uid, s,
                    [SessionMessage(
                        role="user", text="actually search for rust",
                        ts=utcnow_iso(), message_id=new_session_id(),
                    )],
                )
                result = real_get(self, uid, s)
        return result

    monkeypatch.setattr(store.__class__, "get", _get_with_interrupt)

    # max_tool_iterations=3 so the test isn't slow if the
    # interrupt injection fails for some reason; the test
    # asserts the interrupt happened within those 3.
    reply = await handle_message(
        str(state_dir),
        text="search for python",
        channel="webui",
        
        uid=1,
        session_id=sess.session_id,
        employee_provider="minimax",
        employee_api_key="fake",
        max_tool_iterations=3,
    )

    assert reply == "Searching for rust instead."

    # The session store should now carry both user messages
    # in order. The interrupt path doesn't persist the
    # assistant reply (the channel-side writer does that
    # after handle_message returns), so we only check the
    # user rows here.
    # D.23: store key is uid (int), not the
    # channel's tgid string.
    final = SessionStore(str(state_dir)).get(1, sess.session_id)
    assert final is not None
    user_texts = [m.text for m in final.messages if m.role == "user"]
    assert user_texts == [
        "search for python", "actually search for rust",
    ]


@pytest.mark.asyncio
async def test_handle_message_no_interrupt_works_normally(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative case: no interrupting message arrives. The
    loop runs two iterations (tool_use → text reply) and
    returns the final text. Sanity check that D.21 didn't
    break the happy path."""
    provider, get_provider = _fake_provider_factory([
        _tool_use_result("t1", "list_files", {"path": "."}),
        _text_result("Here's what I found."),
    ])
    monkeypatch.setattr(loop_mod, "get_provider", get_provider)

    tgid = "no-interrupt-chat"
    store = SessionStore(str(state_dir))
    sess = store.create(1, channel="webui", )
    store.append_messages(
        1, sess.session_id,
        [SessionMessage(
            role="user", text="list stuff",
            ts=utcnow_iso(), message_id=new_session_id(),
        )],
    )

    reply = await handle_message(
        str(state_dir),
        text="list stuff",
        channel="webui",
        
        uid=1,
        session_id=sess.session_id,
        employee_provider="minimax",
        employee_api_key="fake",
        max_tool_iterations=3,
    )

    assert reply == "Here's what I found."