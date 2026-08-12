"""Unit tests for ``magi.agent.compaction``.

Real :class:`ConversationBook` + :class:`MessageBook` against an
in-memory SQLite. The LLM call is stubbed via
``magi.agent.compaction.call_llm_for_summary`` so no real provider /
job board is required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from magi.agent.compaction import maybe_compact
from magi.bus.db import EngineFactory
from magi.bus.library.local import ConversationBook, MessageBook


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def factory():
    """Fresh in-memory SQLite per test."""
    f = EngineFactory("sqlite:///:memory:")
    f.create_all()
    return f


@pytest.fixture
def contact_id(factory):
    from magi.bus.library.local.contactBook import ContactBook

    return ContactBook(factory).add(name="Fixture").id


@pytest.fixture
def seed_conversation(factory, contact_id):
    """Create a conversation row, return ``(sbook, mbook, conversation_id)``."""
    sbook = ConversationBook(factory)
    mbook = MessageBook(factory)
    cid = "c1"
    sbook.add(
        conversation_id=cid,
        delivery_address="tg:1",
        contact_id=contact_id,
        channel="tg",
        created_at="2026-08-05T00:00:00Z",
        updated_at="2026-08-05T00:00:00Z",
    )
    return sbook, mbook, cid


def _make_bus(*, sbook: ConversationBook, mbook: MessageBook) -> MagicMock:
    """A Bus-like object exposing the books and settings/prompt books
    that ``maybe_compact`` reads."""
    bus = MagicMock()
    bus.conversations_book = sbook
    bus.messages_book = mbook
    # No settings persisted → defaults apply.
    bus.settings_book.get.return_value = None
    # compaction_prompt() is read inside call_llm_for_summary; mock it
    # so the test doesn't depend on the file prompt.
    bus.prompt_book = MagicMock()
    bus.prompt_book.compaction_prompt.return_value = "system: compress"
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
        mbook.add(
            conversation_id=cid,
            message_id=f"m{i:03d}",
            role="user" if i % 2 == 0 else "assistant",
            text="x" * 1200,
            ts=f"2026-08-05T00:00:{i:02d}Z",
        )

    bus = _make_bus(sbook=sbook, mbook=mbook)
    bus.settings_book.get.side_effect = lambda key: {
        "system.compact_keep_recent": 8,
        "system.compact_context_window": 16_000,
        "system.compact_threshold_pct": 50,
    }.get(key)
    stub = _stub_summary(monkeypatch, return_value="NEW SUMMARY")

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
        mbook.add(
            conversation_id=cid,
            message_id=f"m{i:03d}",
            role="user",
            text="x" * 1700,  # 20 × 1700 = 34000 chars = 8500 tokens > 8000 threshold
            ts=f"2026-08-05T00:00:{i:02d}Z",
        )

    bus = _make_bus(sbook=sbook, mbook=mbook)
    bus.settings_book.get.side_effect = lambda key: {
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
    """5 messages (≤ keep_tail=8) → no compaction."""
    sbook, mbook, cid = seed_conversation
    for i in range(5):
        mbook.add(
            conversation_id=cid,
            message_id=f"m{i:03d}",
            role="user",
            text="hi",
            ts=f"2026-08-05T00:00:0{i}Z",
        )

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
        mbook.add(
            conversation_id=cid,
            message_id=f"m{i:03d}",
            role="user",
            text="hi",  # tiny — won't breach threshold
            ts=f"2026-08-05T00:00:{i:02d}Z",
        )

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
        mbook.add(
            conversation_id=cid,
            message_id=f"m{i:03d}",
            role="user",
            text="x" * 1200,
            ts=f"2026-08-05T00:00:{i:02d}Z",
        )

    bus = _make_bus(sbook=sbook, mbook=mbook)
    bus.settings_book.get.side_effect = lambda key: {
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


# ---------------------------------------------------------------------------
# build_messages_from_conversation tests
# ---------------------------------------------------------------------------


async def test_build_messages_prepends_summary(seed_conversation, contact_id):
    """Summary set → returned list[0] is the summary dict."""
    from magi.agent.agent_context import build_messages_from_conversation

    sbook, mbook, cid = seed_conversation
    sbook.set_summary(contact_id=contact_id, conversation_id=cid, summary="S")
    mbook.add(
        conversation_id=cid, message_id="m1", role="user", text="u1",
        ts="2026-08-05T00:00:01Z",
    )
    mbook.add(
        conversation_id=cid, message_id="m2", role="assistant", text="a1",
        ts="2026-08-05T00:00:02Z",
    )

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
    mbook.add(
        conversation_id=cid, message_id="m1", role="user", text="u1",
        ts="2026-08-05T00:00:01Z",
    )

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