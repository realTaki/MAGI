"""Unit tests for the inbound chat-text cap.

Two layers are tested:

  - :meth:`chatJobBoard.publish_chat` — caps the LLM input on the
    way in, sets ``text_truncated`` + ``text_original_len`` flags
    on the payload for downstream LLM/UI consumers.
  - :meth:`MessageBook.add` — caps the persistent message row, so
    compaction (which reads from messages_book) can never be broken
    by a single runaway turn.

Both read the same ``system.chat_max_input_chars`` setting, so
operator changes propagate uniformly. The shared helper is in
:mod:`magi.bus.library.chat_input`.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from magi.bus.db import EngineFactory
from magi.bus.guild.chatJob import chatJobBoard
from magi.bus.library.local import MessageBook


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
    sbook_factory = factory
    from magi.bus.library.local import ConversationBook

    sbook = ConversationBook(sbook_factory)
    cid = "c1"
    sbook.add(
        conversation_id=cid,
        delivery_address="tg:1",
        contact_id=contact_id,
        channel="tg",
        created_at="2026-08-05T00:00:00Z",
        updated_at="2026-08-05T00:00:00Z",
    )
    return cid


# ---------------------------------------------------------------------------
# chatJobBoard.publish_chat
# ---------------------------------------------------------------------------


def _make_chat_board(factory, *, settings_book=None):
    return chatJobBoard(factory, settings_book=settings_book)


def test_publish_chat_noop_under_cap(factory):
    """Under the cap: no truncation, no flags, original text preserved."""
    board = _make_chat_board(factory, settings_book=None)  # uses default 8000
    bigish = "x" * 100  # well under 8000

    jid = board.publish_chat(
        text=bigish,
        channel="tg",
        contact_id=None,
        conversation_id="c1",
    )
    job = board.claim_for_conversation(conversation_id="c1")
    assert job is not None
    assert job.job_id == jid
    assert job.payload is not None
    assert job.payload["text"] == bigish
    assert "text_truncated" not in job.payload
    assert "text_original_len" not in job.payload


def test_publish_chat_truncates_over_cap(factory):
    """Over the cap: text truncated to cap, flags set on the payload."""
    board = _make_chat_board(factory, settings_book=None)
    huge = "x" * 12_000  # over default 8000

    board.publish_chat(
        text=huge,
        channel="tg",
        contact_id=None,
        conversation_id="c1",
    )
    job = board.claim_for_conversation(conversation_id="c1")
    assert job is not None
    payload = job.payload or {}
    assert len(payload["text"]) == 8_000
    assert payload["text"] == "x" * 8_000
    assert payload["text_truncated"] is True
    assert payload["text_original_len"] == 12_000


def test_publish_chat_honors_lowered_cap(factory):
    """Operator sets the cap to 1000; oversize messages get cut to 1000."""
    settings_book = MagicMock()
    settings_book.get.return_value = "1000"
    board = _make_chat_board(factory, settings_book=settings_book)

    board.publish_chat(
        text="y" * 5_000,
        channel="webui",
        contact_id=None,
        conversation_id="c1",
    )
    job = board.claim_for_conversation(conversation_id="c1")
    payload = job.payload or {}
    assert len(payload["text"]) == 1_000
    assert payload["text_original_len"] == 5_000


def test_publish_chat_clamps_out_of_range_settings(factory):
    """Garbage / out-of-range settings values are clamped to the bounds."""
    # 50 below MIN (1000) — clamp to 1000
    settings_book = MagicMock()
    settings_book.get.return_value = "50"
    board = _make_chat_board(factory, settings_book=settings_book)
    board.publish_chat(text="z" * 200, channel="tg", contact_id=None, conversation_id="c1")
    job = board.claim_for_conversation(conversation_id="c1")
    payload = job.payload or {}
    assert len(payload["text"]) == 200  # fits in 1000, no truncation
    assert "text_truncated" not in payload

    # Above MAX (100_000) — clamp to 100_000; 200 chars still fits
    settings_book.get.return_value = "999_999_999"
    board2 = _make_chat_board(factory, settings_book=settings_book)
    board2.publish_chat(text="z" * 200, channel="tg", contact_id=None, conversation_id="c2")
    job2 = board2.claim_for_conversation(conversation_id="c2")
    payload2 = job2.payload or {}
    assert len(payload2["text"]) == 200
    assert "text_truncated" not in payload2


def test_publish_chat_no_settings_book_uses_default(factory):
    """No settings_book passed → fall back to default cap (8000)."""
    board = _make_chat_board(factory, settings_book=None)
    board.publish_chat(text="a" * 9_000, channel="tg", contact_id=None, conversation_id="c1")
    job = board.claim_for_conversation(conversation_id="c1")
    payload = job.payload or {}
    assert len(payload["text"]) == 8_000
    assert payload["text_original_len"] == 9_000


# ---------------------------------------------------------------------------
# MessageBook.add
# ---------------------------------------------------------------------------


def test_messages_book_add_noop_under_cap(factory, seed_conversation):
    mbook = MessageBook(factory, settings_book=None)  # default 8000
    m = mbook.add(conversation_id="c1", message_id="m1", role="user", text="hi")
    assert m.text == "hi"
    assert m.text is not None and len(m.text) == 2


def test_messages_book_add_truncates_over_cap(factory, seed_conversation):
    """Over the cap → stored row is truncated; original length is gone (no flag on row)."""
    mbook = MessageBook(factory, settings_book=None)
    huge = "x" * 20_000

    m = mbook.add(
        conversation_id="c1", message_id="m1", role="user", text=huge
    )
    assert m.text is not None
    assert len(m.text) == 8_000
    # No flag on the row — the flag lives on the chatJob payload only.
    # The row just holds the truncated text.


def test_messages_book_add_honors_lowered_cap(factory, seed_conversation):
    settings_book = MagicMock()
    settings_book.get.return_value = "2000"  # within clamp range 1000-100_000
    mbook = MessageBook(factory, settings_book=settings_book)
    m = mbook.add(
        conversation_id="c1", message_id="m1", role="user", text="y" * 5_000
    )
    assert m.text is not None
    assert len(m.text) == 2_000


def test_messages_book_add_compaction_cant_be_broken_by_huge_turn(
    factory, contact_id
):
    """End-to-end: even with a single huge turn, compaction's floor-of-1
    works because the row stored is already capped.
    """
    from magi.bus.library.local import ConversationBook

    sbook = ConversationBook(factory, settings_book=None)
    sbook.add(
        conversation_id="c1",
        delivery_address="tg:1",
        contact_id=contact_id,
        channel="tg",
        created_at="2026-08-05T00:00:00Z",
        updated_at="2026-08-05T00:00:00Z",
    )

    mbook = MessageBook(factory, settings_book=None)
    mbook.add(
        conversation_id="c1",
        message_id="m_huge",
        role="user",
        text="x" * 50_000,  # would otherwise single-handedly bust the budget
    )

    # Stored row is capped → never > 8000 chars
    rows = mbook.list_for_conversation(conversation_id="c1")
    assert len(rows) == 1
    assert len(rows[0].text) == 8_000


# ---------------------------------------------------------------------------
# chat_input helper unit tests
# ---------------------------------------------------------------------------


def test_resolve_max_input_chars_with_none_settings():
    from magi.bus.library.chat_input import resolve_max_input_chars

    assert resolve_max_input_chars(None) == 8_000


def test_resolve_max_input_chars_clamps_garbage():
    from magi.bus.library.chat_input import resolve_max_input_chars

    sb = MagicMock()
    sb.get.return_value = "not-a-number"
    assert resolve_max_input_chars(sb) == 8_000  # garbage → default

    sb.get.return_value = "50"  # below MIN 1000
    assert resolve_max_input_chars(sb) == 1_000

    sb.get.return_value = "999_999_999"  # above MAX 100_000
    assert resolve_max_input_chars(sb) == 100_000


def test_truncate_text_under():
    from magi.bus.library.chat_input import truncate_text

    text, was_truncated, original = truncate_text("hello", 100)
    assert text == "hello"
    assert was_truncated is False
    assert original == 5


def test_truncate_text_over():
    from magi.bus.library.chat_input import truncate_text

    text, was_truncated, original = truncate_text("x" * 200, 50)
    assert len(text) == 50
    assert was_truncated is True
    assert original == 200