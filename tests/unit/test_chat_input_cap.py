"""Unit tests for the inbound chat-text chokepoint.

Two concerns, **separated by layer**:

  1. **Cap** (the ``system.chat_max_input_chars`` setting) — enforced
     at :meth:`MessageBook.add` because the cap is fundamentally a
     *write-side* concern. The LLM reads from ``chat_messages`` (the
     post-cap row), not from a chatJob payload; the chatJob layer
     therefore has no parallel cap to keep in sync.
  2. **D.22 cross-channel guard + consolidated user-message write** —
     :meth:`chatJobBoard.publish_chat` enforces the guard and writes
     the user message to ``chat_messages`` at the same chokepoint as
     the chatJob enqueue. Channels (TG / WebUI / Task) never reach
     into ``messages_book`` directly.

Helpers ``_truncate_inbound`` / ``_resolve_max_input_chars`` live
in :mod:`magi.bus.library.local.conversationBook` (the Book that
uses them).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from magi.bus.db import EngineFactory
from magi.bus.guild.chatJob import chatJobBoard
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
    from magi.bus.library.local import ConversationBook

    sbook = ConversationBook(factory)
    return sbook.add(
        delivery_address="tg:1",
        contact_id=contact_id,
        channel="tg",
    ).conversation_id


# ---------------------------------------------------------------------------
# MessageBook.add — the single chokepoint for the cap
# ---------------------------------------------------------------------------


def test_messages_book_add_noop_under_cap(factory, seed_conversation):
    mbook = MessageBook(factory, settings_book=None)
    m = mbook.add(conversation_id=seed_conversation, message_id="m1", role="user", text="hi")
    assert m.text == "hi"


def test_messages_book_add_truncates_over_cap(factory, seed_conversation):
    """Over the cap → stored row is truncated."""
    mbook = MessageBook(factory, settings_book=None)
    huge = "x" * 20_000
    m = mbook.add(
        conversation_id=seed_conversation, message_id="m1", role="user", text=huge
    )
    assert m.text is not None
    assert len(m.text) == 8_000  # default cap


def test_messages_book_add_honors_lowered_cap(factory, seed_conversation):
    settings_book = MagicMock()
    settings_book.get.return_value = "2000"  # within clamp range
    mbook = MessageBook(factory, settings_book=settings_book)
    m = mbook.add(
        conversation_id=seed_conversation, message_id="m1", role="user", text="y" * 5_000
    )
    assert m.text is not None
    assert len(m.text) == 2_000


def test_messages_book_add_compaction_cant_be_broken_by_huge_turn(
    factory, contact_id
):
    """End-to-end: even with a single huge turn, compaction's floor-of-1
    works because the row stored is already capped.
    """
    sbook = ConversationBook(factory, settings_book=None)
    conv = sbook.add(
        delivery_address="tg:1",
        contact_id=contact_id,
        channel="tg",
    )
    cid = conv.conversation_id
    mbook = MessageBook(factory, settings_book=None)
    mbook.add(
        conversation_id=cid,
        message_id="m_huge",
        role="user",
        text="x" * 50_000,
    )
    rows = mbook.list_for_conversation(conversation_id=cid)
    assert len(rows) == 1
    assert len(rows[0].text) == 8_000


# ---------------------------------------------------------------------------
# Module-private helpers in conversationBook
# ---------------------------------------------------------------------------


def test_resolve_max_input_chars_with_none_settings():
    from magi.bus.library.local.conversationBook import _resolve_max_input_chars

    assert _resolve_max_input_chars(None) == 8_000


def test_resolve_max_input_chars_clamps_garbage():
    from magi.bus.library.local.conversationBook import _resolve_max_input_chars

    sb = MagicMock()
    sb.get.return_value = "not-a-number"
    assert _resolve_max_input_chars(sb) == 8_000

    sb.get.return_value = "50"  # below MIN
    assert _resolve_max_input_chars(sb) == 1_000

    sb.get.return_value = "999_999_999"  # above MAX
    assert _resolve_max_input_chars(sb) == 100_000


def test_truncate_inbound_under():
    from magi.bus.library.local.conversationBook import _truncate_inbound

    text, was_truncated, original = _truncate_inbound("hello", 100)
    assert text == "hello"
    assert was_truncated is False
    assert original == 5


def test_truncate_inbound_over():
    from magi.bus.library.local.conversationBook import _truncate_inbound

    text, was_truncated, original = _truncate_inbound("x" * 200, 50)
    assert len(text) == 50
    assert was_truncated is True
    assert original == 200


# ---------------------------------------------------------------------------
# chatJobBoard — consolidated chokepoint (D.22 + writes user message)
# ---------------------------------------------------------------------------


def test_publish_chat_does_not_cap_payload(factory, contact_id):
    """The cap moved to MessageBook. The chatJob payload.text is the
    *raw* user input — the LLM never reads it; compaction / LLM read
    from messages_book, which is what truncates.
    """
    sbook = ConversationBook(factory)
    mbook = MessageBook(factory)
    conv = sbook.add(
        delivery_address="tg:1",
        contact_id=contact_id,
        channel="tg",
    )
    cid = conv.conversation_id
    board = chatJobBoard(
        factory, messages_book=mbook, conversations_book=sbook
    )

    huge = "x" * 20_000
    board.publish_chat(
        text=huge,
        channel="tg",
        contact_id=contact_id,
        conversation_id=cid,
    )
    job = board.claim_for_conversation(conversation_id=cid)
    # ChatJob is typed — no payload dict, no truncation flag. The
    # raw text travels through the row, intact.
    assert job.text == huge
    assert job.channel == "tg"
    # But messages_book row IS truncated (cap lives there).
    rows = mbook.list_for_conversation(conversation_id=cid)
    assert len(rows[0].text) == 8_000


def test_publish_chat_writes_user_message_to_messages_book(factory, contact_id):
    """A single publish_chat call writes the chatJob row AND the
    user-message row. Channels don't reach into messages_book
    directly anymore.
    """
    sbook = ConversationBook(factory)
    mbook = MessageBook(factory)
    conv = sbook.add(
        delivery_address="tg:1",
        contact_id=contact_id,
        channel="tg",
    )
    cid = conv.conversation_id
    board = chatJobBoard(
        factory,
        messages_book=mbook,
        conversations_book=sbook,
    )

    jid = board.publish_chat(
        text="hello world",
        channel="tg",
        contact_id=contact_id,
        conversation_id=cid,
    )
    assert jid

    job = board.claim_for_conversation(conversation_id=cid)
    assert job is not None
    assert job.text == "hello world"
    assert job.channel == "tg"
    assert job.contact_id == contact_id

    rows = mbook.list_for_conversation(conversation_id=cid)
    assert len(rows) == 1
    assert rows[0].text == "hello world"
    assert rows[0].role == "user"


def test_publish_chat_uses_message_id_for_idempotency(factory, contact_id):
    """Same message_id on retry → same chat_messages row (producer-side idempotency)."""
    sbook = ConversationBook(factory)
    mbook = MessageBook(factory)
    conv = sbook.add(
        delivery_address="tg:1",
        contact_id=contact_id,
        channel="tg",
    )
    cid = conv.conversation_id
    board = chatJobBoard(
        factory, messages_book=mbook, conversations_book=sbook
    )

    fixed_id = "fixed-message-id-123"
    board.publish_chat(
        text="retry me",
        channel="tg",
        contact_id=contact_id,
        conversation_id=cid,
        message_id=fixed_id,
    )
    board.publish_chat(
        text="retry me",
        channel="tg",
        contact_id=contact_id,
        conversation_id=cid,
        message_id=fixed_id,
    )

    rows = mbook.list_for_conversation(conversation_id=cid)
    # Unique constraint on (conversation_id, message_id) collapses
    # the retry into a single row.
    assert len(rows) == 1


def test_publish_chat_d22_raises_on_channel_mismatch(factory, contact_id):
    """D.22: conversation created on TG, caller publishes as webui → ChannelMismatchError."""
    sbook = ConversationBook(factory)
    mbook = MessageBook(factory)
    conv = sbook.add(
        delivery_address="tg:1",
        contact_id=contact_id,
        channel="tg",
    )
    cid = conv.conversation_id
    board = chatJobBoard(
        factory, messages_book=mbook, conversations_book=sbook
    )

    from magi.bus.library.local.conversationBook import ChannelMismatchError

    with pytest.raises(ChannelMismatchError) as exc:
        board.publish_chat(
            text="cross-channel write",
            channel="webui",
            contact_id=contact_id,
            conversation_id=cid,
        )
    assert exc.value.conversation_channel == "tg"

    # No chatJob, no message row — the guard fires before either write.
    assert board.claim_for_conversation(conversation_id=cid) is None
    rows = mbook.list_for_conversation(conversation_id=cid)
    assert len(rows) == 0


def test_publish_chat_d22_passes_when_channel_matches(factory, contact_id):
    """D.22: same channel → no error, both writes happen."""
    sbook = ConversationBook(factory)
    mbook = MessageBook(factory)
    conv = sbook.add(
        delivery_address="tg:1",
        contact_id=contact_id,
        channel="tg",
    )
    cid = conv.conversation_id
    board = chatJobBoard(
        factory, messages_book=mbook, conversations_book=sbook
    )

    jid = board.publish_chat(
        text="normal",
        channel="tg",
        contact_id=contact_id,
        conversation_id=cid,
    )
    assert jid
    assert len(mbook.list_for_conversation(conversation_id=cid)) == 1


def test_publish_chat_d22_skipped_when_contact_id_is_none(factory):
    """Task path: no contact_id → D.22 guard skipped."""
    sbook = ConversationBook(factory)
    mbook = MessageBook(factory)
    conv = sbook.add(
        delivery_address="tg:1",
        contact_id=1,
        channel="tg",
    )
    cid = conv.conversation_id
    board = chatJobBoard(
        factory, messages_book=mbook, conversations_book=sbook
    )

    jid = board.publish_chat(
        text="task fire",
        channel="task",
        contact_id=None,
        conversation_id=cid,
    )
    assert jid
    assert len(mbook.list_for_conversation(conversation_id=cid)) == 1


def test_publish_chat_d22_skipped_when_no_conversations_book(factory, contact_id):
    """Backward-compat: board constructed without conversations_book → no D.22 check."""
    sbook = ConversationBook(factory)
    mbook = MessageBook(factory)
    conv = sbook.add(
        delivery_address="tg:1",
        contact_id=contact_id,
        channel="tg",
    )
    cid = conv.conversation_id
    board = chatJobBoard(factory, messages_book=mbook)  # no conversations_book

    jid = board.publish_chat(
        text="no-d22",
        channel="webui",
        contact_id=contact_id,
        conversation_id=cid,
    )
    assert jid
    assert len(mbook.list_for_conversation(conversation_id=cid)) == 1


# ---------------------------------------------------------------------------
# publish() (lower-level, used by submit_agent_message etc.) gets the
# same D.22 treatment, no messages_book write.
# ---------------------------------------------------------------------------


def test_publish_direct_enforces_d22(factory, contact_id):
    """Direct :meth:`publish` callers (e.g. :func:`submit_agent_message`
    for internal steering republishes) get the same D.22 guard.
    """
    from magi.bus.guild.chatJob import ChatJob

    sbook = ConversationBook(factory)
    conv = sbook.add(
        delivery_address="tg:1",
        contact_id=contact_id,
        channel="tg",
    )
    cid = conv.conversation_id
    board = chatJobBoard(factory, conversations_book=sbook)

    job = ChatJob(
        job_id="steer-1",
        conversation_id=cid,
        text="x",
        channel="webui",
        contact_id=contact_id,
    )

    from magi.bus.library.local.conversationBook import ChannelMismatchError

    with pytest.raises(ChannelMismatchError):
        board.publish(job)