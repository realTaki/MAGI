"""Unit tests for ``magi.agent.compaction``.

Real :class:`ConversationBook` + :class:`MessageBook` against an
in-memory SQLite. The LLM call is stubbed via
``magi.agent.compaction.call_llm_for_summary`` so no real provider /
job board is required.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from magi.agent.compaction import maybe_compact
from magi.old_bus.bases.db import EngineFactory
from magi.old_bus.firmwares.books.local import (
    Contact,
    Conversation,
    ConversationBook,
    Message,
    MessageBook,
    SettingBook,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def factory():
    """Fresh in-memory SQLite per test."""
    f = EngineFactory("sqlite:///:memory:")
    f.create_all()
    return f


def _seeded_settings_book(factory) -> SettingBook:
    """Return a ``SettingBook`` with the standard channel set registered.

    Helper for tests that build a ``ConversationBook`` from the raw factory
    fixture — they need a ``settings_book`` wired in so
    ``ConversationBook._validate_add`` can read ``channel_options()`` and
    reject unregistered channels. Mirrors runtime bootstrap.
    """
    sbook = SettingBook(factory)
    for name in ("a2a", "tg", "webui", "task"):
        sbook.register_channel(name=name)
    return sbook


@pytest.fixture
def contact_id(factory):
    from magi.old_bus.firmwares.books.local.contactBook import ContactBook

    return ContactBook(factory).get(ContactBook(factory).add(Contact(name='Fixture'))).id


@pytest.fixture
def seed_conversation(factory, contact_id):
    """Create a conversation row, return ``(sbook, mbook, conversation_id)``."""
    sbook = ConversationBook(factory, settings_book=_seeded_settings_book(factory))
    mbook = MessageBook(factory)
    cid = sbook.get(sbook.add(Conversation(delivery_address='tg:1', contact_id=contact_id, channel='tg'))).id
    return sbook, mbook, cid


def _make_bus(*, sbook: ConversationBook, mbook: MessageBook) -> MagicMock:
    """A Bus-like object exposing the books and settings/prompt books
    that ``maybe_compact`` reads."""
    bus = MagicMock()
    bus.conversations_book = sbook
    bus.messages_book = mbook
    # No settings persisted → defaults apply.
    bus.settings_book.get_value.return_value = None
    # The compaction prompt is read through the generic PromptBook KV API;
    # mock it so the test doesn't depend on the file prompt.
    # so the test doesn't depend on the file prompt.
    bus.prompt_book = MagicMock()
    bus.prompt_book.get.return_value = "system: compress"
    return bus


def _stub_summary(monkeypatch, return_value: str | None) -> AsyncMock:
    """Patch ``call_llm_for_summary`` to return a fixed string (or None)."""
    stub = AsyncMock(return_value=return_value)
    monkeypatch.setattr("magi.agent.compaction.call_llm_for_summary", stub)
    return stub


# ---------------------------------------------------------------------------
# maybe_compact tests
# ---------------------------------------------------------------------------


async def test_maybe_compact_archives_and_persists_summary(
    monkeypatch, seed_conversation, contact_id, factory
):
    """30 messages above threshold → 22 archived, summary persisted, returned list = 1 + 8."""
    sbook, mbook, cid = seed_conversation

    # Seed 30 active messages with enough text to breach threshold.
    # Force keep_recent=8 + minimum context_window/threshold so the
    # numbers in the asserts hold (1 summary + 8 tail = 9 entries,
    # 22 archived, 8 still active) and the threshold is reachable
    # by ~30k chars of text. With min context_window=16000 and
    # min threshold_pct=50 → threshold = 8000 tokens; 30 × 1200 chars
    # = 9000 text tokens + 30 × 4 overhead = 9120 tokens ✓.
    for i in range(30):
        mbook.get(mbook.add(Message(conversation_id=cid, role='user' if i % 2 == 0 else 'assistant', text='x' * 1200, ts=datetime(2026, 8, 5, 0, 0, i))))

    bus = _make_bus(sbook=sbook, mbook=mbook)
    bus.settings_book.get_value.side_effect = lambda key: {
        "system.compact_keep_recent": 8,
        "system.compact_context_window": 16_000,
        "system.compact_threshold_pct": 50,
    }.get(key)
    _stub_summary(monkeypatch, return_value="NEW SUMMARY")

    dtos = mbook.list_for_conversation(conversation_id=cid, include_archived=False)
    assert len(dtos) == 30

    result = await maybe_compact(contact_id, cid, dtos, bus=bus)

    # Returned dict list: 1 summary + 8 tail = 9 entries
    assert result is not None
    assert len(result) == 9
    assert result[0]["role"] == "user"
    assert "[Prior conversation summary]" in result[0]["content"]
    assert "NEW SUMMARY" in result[0]["content"]
    assert result[0]["content"].startswith("[Prior conversation summary]\nNEW SUMMARY")
    # tail = last 8 of original 30 → m022..m029
    assert "x" * 1000 in result[-1]["content"]

    # DB: summary + last_compaction_at persisted
    conv = sbook.get_for_owner(contact_id=contact_id, conversation_id=cid)
    assert conv is not None
    assert conv.summary == "NEW SUMMARY"
    assert conv.last_compaction_at is not None

    # 22 rows archived, 8 still active
    all_msgs = mbook.list_for_conversation(conversation_id=cid, include_archived=True)
    active_msgs = mbook.list_for_conversation(conversation_id=cid, include_archived=False)
    assert len(all_msgs) == 30
    assert len(active_msgs) == 8
    # The oldest 22 (m000..m021) are archived
    archived_ids = sorted(m.id for m in all_msgs if m.archived == 1)
    assert len(archived_ids) == 22


async def test_maybe_compact_uses_prior_summary(monkeypatch, seed_conversation, contact_id):
    """Pre-set summary="PREV" → LLM input contains the prior summary; final summary supersedes."""
    sbook, mbook, cid = seed_conversation

    # Pre-seed summary directly on the conversation
    sbook.set_summary(contact_id=contact_id, conversation_id=cid, summary="PREV")

    for i in range(20):
        mbook.get(mbook.add(Message(conversation_id=cid, role='user', text='x' * 1700, ts=datetime(2026, 8, 5, 0, 0, i))))

    bus = _make_bus(sbook=sbook, mbook=mbook)
    bus.settings_book.get_value.side_effect = lambda key: {
        "system.compact_keep_recent": 8,
        "system.compact_context_window": 16_000,
        "system.compact_threshold_pct": 50,
    }.get(key)
    stub = _stub_summary(monkeypatch, return_value="NEW")

    dtos = mbook.list_for_conversation(conversation_id=cid, include_archived=False)
    result = await maybe_compact(contact_id, cid, dtos, bus=bus)

    assert result is not None
    # LLM saw the prior summary as part of `to_compress`
    assert stub.await_count == 1
    sent = stub.await_args.kwargs["to_compress"]
    assert "[Prior summary]\nPREV" in sent
    # The most recent to-archive text is also in the input
    assert "[USER]" in sent

    # DB: summary overwritten with NEW
    conv = sbook.get_for_owner(contact_id=contact_id, conversation_id=cid)
    assert conv is not None
    assert conv.summary == "NEW"


async def test_maybe_compact_noop_under_keep_tail(monkeypatch, seed_conversation, contact_id):
    """5 tiny messages → no compaction (token budget not exceeded)."""
    sbook, mbook, cid = seed_conversation
    for i in range(5):
        mbook.get(mbook.add(Message(conversation_id=cid, role='user', text='hi', ts=datetime(2026, 8, 5, 0, 0, i))))

    bus = _make_bus(sbook=sbook, mbook=mbook)
    stub = _stub_summary(monkeypatch, return_value="NEW")

    dtos = mbook.list_for_conversation(conversation_id=cid, include_archived=False)
    result = await maybe_compact(contact_id, cid, dtos, bus=bus)

    assert result is None
    assert stub.await_count == 0  # never called the LLM

    conv = sbook.get_for_owner(contact_id=contact_id, conversation_id=cid)
    assert conv is not None
    assert conv.summary is None

    active = mbook.list_for_conversation(conversation_id=cid, include_archived=False)
    assert len(active) == 5


async def test_maybe_compact_noop_under_threshold(
    monkeypatch, seed_conversation, contact_id
):
    """12 messages (above keep_tail but tiny text) → token check skips."""
    sbook, mbook, cid = seed_conversation
    for i in range(12):
        mbook.get(mbook.add(Message(conversation_id=cid, role='user', text='hi', ts=datetime(2026, 8, 5, 0, 0, i))))

    bus = _make_bus(sbook=sbook, mbook=mbook)
    stub = _stub_summary(monkeypatch, return_value="NEW")

    dtos = mbook.list_for_conversation(conversation_id=cid, include_archived=False)
    result = await maybe_compact(contact_id, cid, dtos, bus=bus)

    assert result is None
    assert stub.await_count == 0  # threshold skip — never reached LLM


async def test_maybe_compact_returns_none_on_summary_failure(
    monkeypatch, seed_conversation, contact_id
):
    """If the LLM returns None/empty, return None and leave DB untouched."""
    sbook, mbook, cid = seed_conversation
    for i in range(20):
        mbook.get(mbook.add(Message(conversation_id=cid, role='user', text='x' * 1200, ts=datetime(2026, 8, 5, 0, 0, i))))

    bus = _make_bus(sbook=sbook, mbook=mbook)
    bus.settings_book.get_value.side_effect = lambda key: {
        "system.compact_keep_recent": 8,
        "system.compact_context_window": 16_000,
        "system.compact_threshold_pct": 50,
    }.get(key)
    _stub_summary(monkeypatch, return_value=None)

    dtos = mbook.list_for_conversation(conversation_id=cid, include_archived=False)
    result = await maybe_compact(contact_id, cid, dtos, bus=bus)

    assert result is None
    conv = sbook.get_for_owner(contact_id=contact_id, conversation_id=cid)
    assert conv is not None
    assert conv.summary is None
    # No rows archived
    active = mbook.list_for_conversation(conversation_id=cid, include_archived=False)
    assert len(active) == 20


async def test_maybe_compact_shrinks_tail_to_fit_budget(
    monkeypatch, seed_conversation, contact_id
):
    """If the last N messages alone are still over budget, drop from
    the front of the tail until summary + tail fits. Always keep the
    most recent turn.
    """
    sbook, mbook, cid = seed_conversation

    # 20 messages, each 2000 chars = ~504 tokens each (2000/4 + 4 overhead).
    # keep_recent=20, context_window=16_000, threshold_pct=50 → threshold = 8000.
    # 20 × 504 = 10080 > 8000 → compact.
    # candidate_tail = last 20 = 10080 tokens → still over.
    # Drop from front until ≤ 8000: 8000 / 504 ≈ 15.87 → keep 15.
    for i in range(20):
        mbook.get(mbook.add(Message(conversation_id=cid, role='user', text='x' * 2000, ts=datetime(2026, 8, 5, 0, 0, i))))

    bus = _make_bus(sbook=sbook, mbook=mbook)
    bus.settings_book.get_value.side_effect = lambda key: {
        "system.compact_keep_recent": 20,
        "system.compact_context_window": 16_000,
        "system.compact_threshold_pct": 50,
    }.get(key)
    _stub_summary(monkeypatch, return_value="NEW")

    dtos = mbook.list_for_conversation(conversation_id=cid, include_archived=False)
    result = await maybe_compact(contact_id, cid, dtos, bus=bus)

    assert result is not None
    # 1 summary + 15 tail = 16 entries (shrunk from 20)
    assert len(result) == 16
    assert result[0]["content"].startswith("[Prior conversation summary]\nNEW")
    assert result[-1]["content"] == "x" * 2000  # the most recent

    # DB: oldest 5 (m000..m004) archived; m005..m019 still active
    all_msgs = mbook.list_for_conversation(conversation_id=cid, include_archived=True)
    active_msgs = mbook.list_for_conversation(conversation_id=cid, include_archived=False)
    assert len(all_msgs) == 20
    assert len(active_msgs) == 15
    # Active are the most recent 15 (m005..m019)
    assert all(m.archived == 0 for m in active_msgs)
    assert active_msgs[0].ts == datetime(2026, 8, 5, 0, 0, 5)
    assert active_msgs[-1].ts == datetime(2026, 8, 5, 0, 0, 19)


async def test_maybe_compact_keeps_at_least_one_when_summary_fills_budget(
    monkeypatch, seed_conversation, contact_id
):
    """Even if summary + 1 message is over budget, still keep the
    most recent turn (don't drop to zero). Logged as a warning.

    Setup: pre-set a giant summary that nearly fills the threshold
    (32k chars ≈ 8004 tokens, threshold = 8000), then add 2 modest
    messages. The shrink loop ends with len=1 and post still over
    budget — we keep the 1 anyway.
    """
    sbook, mbook, cid = seed_conversation

    # Pre-fill summary to almost-fill the budget. 32_000 chars
    # → 32_000/4 + 4 overhead = 8004 tokens. threshold = 8000.
    sbook.set_summary(contact_id=contact_id, conversation_id=cid, summary="S" * 32_000)

    mbook.get(mbook.add(Message(conversation_id=cid, role='user', text='x' * 100, ts=datetime(2026, 8, 5, 0, 0, 0))))
    mbook.get(mbook.add(Message(conversation_id=cid, role='user', text='x' * 100, ts=datetime(2026, 8, 5, 0, 0, 1))))

    bus = _make_bus(sbook=sbook, mbook=mbook)
    bus.settings_book.get_value.side_effect = lambda key: {
        "system.compact_keep_recent": 5,
        "system.compact_context_window": 16_000,
        "system.compact_threshold_pct": 50,
    }.get(key)
    _stub_summary(monkeypatch, return_value="NEW")

    dtos = mbook.list_for_conversation(conversation_id=cid, include_archived=False)
    result = await maybe_compact(contact_id, cid, dtos, bus=bus)

    # Even with summary + 1 msg over budget, the floor of 1 keeps m001.
    assert result is not None
    assert len(result) == 2  # summary + 1 tail
    # Oldest 1 (m000) archived; m001 still active
    active = mbook.list_for_conversation(conversation_id=cid, include_archived=False)
    assert len(active) == 1
    assert active[0].ts == datetime(2026, 8, 5, 0, 0, 1)


# ---------------------------------------------------------------------------
# build_messages_from_conversation tests
# ---------------------------------------------------------------------------


async def test_build_messages_prepends_summary(seed_conversation, contact_id):
    """Summary set → returned list[0] is the summary dict."""
    from magi.agent.agent_context import build_messages_from_conversation

    sbook, mbook, cid = seed_conversation
    sbook.set_summary(contact_id=contact_id, conversation_id=cid, summary="S")
    mbook.get(mbook.add(Message(conversation_id=cid, role='user', text='u1', ts=datetime(2026, 8, 5, 0, 0, 1))))
    mbook.get(mbook.add(Message(conversation_id=cid, role='assistant', text='a1', ts=datetime(2026, 8, 5, 0, 0, 2))))

    bus = MagicMock()
    bus.conversations_book = sbook
    bus.messages_book = mbook
    msgs = build_messages_from_conversation(
        contact_id=contact_id, conversation_id=cid, new_user_text="new",
        bus=bus,
    )

    assert len(msgs) == 4
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "[Prior conversation summary]\nS"
    assert msgs[1]["content"] == "u1"
    assert msgs[2]["content"] == "a1"
    assert msgs[3]["content"] == "new"  # appended new user text


async def test_build_messages_no_summary(seed_conversation, contact_id):
    """summary=None → no summary dict prepended."""
    from magi.agent.agent_context import build_messages_from_conversation

    sbook, mbook, cid = seed_conversation
    mbook.get(mbook.add(Message(conversation_id=cid, role='user', text='u1', ts=datetime(2026, 8, 5, 0, 0, 1))))

    bus = MagicMock()
    bus.conversations_book = sbook
    bus.messages_book = mbook
    msgs = build_messages_from_conversation(
        contact_id=contact_id, conversation_id=cid, new_user_text="new",
        bus=bus,
    )

    # No summary prepended; just user + new
    assert len(msgs) == 2
    assert msgs[0]["content"] == "u1"
    assert msgs[1]["content"] == "new"
    assert "summary" not in msgs[0]["content"].lower() or "u1" in msgs[0]["content"]
