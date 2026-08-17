"""Unit tests for durable chat publication and unmodified BUS storage.

Input limits belong to API/channel entry points.  ``MessageBook`` persists
the exact payload supplied by its caller; it must not implement a second
character-count policy.
"""

from __future__ import annotations

import pytest

from magi.bus.bases.db import EngineFactory
from magi.bus.bases.job import JobStatus
from magi.bus.firmwares.jobs.chatNotifyJob import ChatNotifyJob, ChatNotifyResult, chatNotifyBoard
from magi.bus.firmwares.books.local import (
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
    from magi.bus.firmwares.books.local.contactBook import ContactBook

    return ContactBook(factory).get(ContactBook(factory).add(Contact(name='Fixture'))).id


@pytest.fixture
def seed_conversation(factory, contact_id):
    from magi.bus.firmwares.books.local import ConversationBook

    sbook = ConversationBook(factory, settings_book=_seeded_settings_book(factory))
    return sbook.get(sbook.add(Conversation(delivery_address='tg:1', contact_id=contact_id, channel='tg'))).id


# ---------------------------------------------------------------------------
# MessageBook.add preserves payload text
# ---------------------------------------------------------------------------


def test_messages_book_add_noop_under_cap(factory, seed_conversation):
    mbook = MessageBook(factory, settings_book=None)
    m = mbook.get(mbook.add(Message(conversation_id=seed_conversation, role='user', text='hi')))
    assert m.text == "hi"


def test_messages_book_add_preserves_long_text(factory, seed_conversation):
    mbook = MessageBook(factory, settings_book=None)
    huge = "x" * 20_000
    m = mbook.get(mbook.add(Message(conversation_id=seed_conversation, role='user', text=huge)))
    assert m.text is not None
    assert m.text == huge


def test_messages_book_add_keeps_huge_turn_for_provider_budgeting(
    factory, contact_id
):
    """Compaction/provider code, not persistence, decides how to budget it."""
    sbook = ConversationBook(factory, settings_book=None)
    conv = sbook.get(sbook.add(Conversation(delivery_address='tg:1', contact_id=contact_id, channel='tg')))
    cid = conv.id
    mbook = MessageBook(factory, settings_book=None)
    mbook.get(mbook.add(Message(conversation_id=cid, role='user', text='x' * 50000)))
    rows = mbook.list_for_conversation(conversation_id=cid)
    assert len(rows) == 1
    assert len(rows[0].text) == 50_000


# ---------------------------------------------------------------------------
# chatNotifyBoard — consolidated chokepoint (D.22 + writes user message)
# ---------------------------------------------------------------------------


def test_publish_chat_preserves_payload_in_book(factory, contact_id):
    sbook = ConversationBook(factory, settings_book=_seeded_settings_book(factory))
    mbook = MessageBook(factory)
    conv = sbook.get(sbook.add(Conversation(delivery_address='tg:1', contact_id=contact_id, channel='tg')))
    cid = conv.id
    board = chatNotifyBoard(
        factory, messages_book=mbook, conversations_book=sbook
    )

    huge = "x" * 20_000
    board.publish(
        ChatNotifyJob(
            text=huge,
            channel="tg",
            contact_id=contact_id,
            conversation_id=cid,
        )
    )
    job = board.claim_for_steering(conversation_id=cid, worker_id="agent-a")
    # ChatNotifyJob is typed — no payload dict, no truncation flag. The
    # raw text travels through the row, intact.
    assert job.text == huge
    assert job.channel == "tg"
    # The stored row is equally unmodified; the provider boundary budgets it.
    rows = mbook.list_for_conversation(conversation_id=cid)
    assert rows[0].text == huge


def test_publish_chat_writes_user_message_to_messages_book(factory, contact_id):
    """A single publish_chat call writes the chatNotifyJob row AND the
    user-message row. Channels don't reach into messages_book
    directly anymore.
    """
    sbook = ConversationBook(factory, settings_book=_seeded_settings_book(factory))
    mbook = MessageBook(factory)
    conv = sbook.get(sbook.add(Conversation(delivery_address='tg:1', contact_id=contact_id, channel='tg')))
    cid = conv.id
    board = chatNotifyBoard(
        factory,
        messages_book=mbook,
        conversations_book=sbook,
    )

    jid = board.publish(
        ChatNotifyJob(
            text="hello world",
            channel="tg",
            contact_id=contact_id,
            conversation_id=cid,
        )
    )
    assert jid

    job = board.claim_for_steering(conversation_id=cid, worker_id="agent-a")
    assert job is not None
    assert job.text == "hello world"
    assert job.channel == "tg"
    assert job.contact_id == contact_id

    rows = mbook.list_for_conversation(conversation_id=cid)
    assert len(rows) == 1
    assert rows[0].text == "hello world"
    assert rows[0].role == "user"


def test_check_job_status_exposes_claim_as_channel_receipt(factory, contact_id):
    """A Channel observes Agent receipt at the atomic claim, not completion."""
    sbook = ConversationBook(factory, settings_book=_seeded_settings_book(factory))
    mbook = MessageBook(factory)
    conv = sbook.get(sbook.add(Conversation(
        delivery_address="tg:1", contact_id=contact_id, channel="tg"
    )))
    board = chatNotifyBoard(factory, messages_book=mbook, conversations_book=sbook)

    job_id = board.publish(ChatNotifyJob(
        text="hello",
        channel="tg",
        contact_id=contact_id,
        conversation_id=conv.id,
    ))
    assert board.check_job_status(job_id=job_id) is JobStatus.PENDING
    assert board.check_job_status(job_id=job_id + 10_000) is None

    job = board.claim_for_steering(conversation_id=conv.id, worker_id="agent-a")
    assert job is not None
    assert board.check_job_status(job_id=job_id) is JobStatus.PROCESSING

    board.submit_result(
        job_id=job_id,
        worker_id="agent-a",
        result=ChatNotifyResult(job_id=job_id, status=JobStatus.COMPLETED),
    )
    assert board.check_job_status(job_id=job_id) is JobStatus.COMPLETED


def test_publish_chat_writes_one_message_per_turn(factory, contact_id):
    """Each publish persists one user message row (no producer-side dedup key)."""
    sbook = ConversationBook(factory, settings_book=_seeded_settings_book(factory))
    mbook = MessageBook(factory)
    conv = sbook.get(sbook.add(Conversation(delivery_address='tg:1', contact_id=contact_id, channel='tg')))
    cid = conv.id
    board = chatNotifyBoard(
        factory, messages_book=mbook, conversations_book=sbook
    )

    board.publish(
        ChatNotifyJob(
            text="retry me",
            channel="tg",
            contact_id=contact_id,
            conversation_id=cid,
        )
    )
    board.publish(
        ChatNotifyJob(
            text="retry me",
            channel="tg",
            contact_id=contact_id,
            conversation_id=cid,
        )
    )

    rows = mbook.list_for_conversation(conversation_id=cid)
    # No idempotency key for ordinary chat turns — two publishes
    # mean two rows (the agent loop owns de-duplication, not the board).
    assert len(rows) == 2


def test_publish_chat_d22_raises_on_channel_mismatch(factory, contact_id):
    """D.22: conversation created on TG, caller publishes as webui → ChannelMismatchError."""
    sbook = ConversationBook(factory, settings_book=_seeded_settings_book(factory))
    mbook = MessageBook(factory)
    conv = sbook.get(sbook.add(Conversation(delivery_address='tg:1', contact_id=contact_id, channel='tg')))
    cid = conv.id
    board = chatNotifyBoard(
        factory, messages_book=mbook, conversations_book=sbook
    )

    from magi.bus.firmwares.books.local.conversationBook import ChannelMismatchError

    with pytest.raises(ChannelMismatchError) as exc:
        board.publish(
            ChatNotifyJob(
                text="cross-channel write",
                channel="webui",
                contact_id=contact_id,
                conversation_id=cid,
            )
        )
    assert exc.value.conversation_channel == "tg"

    # No chatNotifyJob, no message row — the guard fires before either write.
    assert board.claim_for_steering(conversation_id=cid, worker_id="agent-a") is None
    rows = mbook.list_for_conversation(conversation_id=cid)
    assert len(rows) == 0


def test_publish_chat_d22_passes_when_channel_matches(factory, contact_id):
    """D.22: same channel → no error, both writes happen."""
    sbook = ConversationBook(factory, settings_book=_seeded_settings_book(factory))
    mbook = MessageBook(factory)
    conv = sbook.get(sbook.add(Conversation(delivery_address='tg:1', contact_id=contact_id, channel='tg')))
    cid = conv.id
    board = chatNotifyBoard(
        factory, messages_book=mbook, conversations_book=sbook
    )

    jid = board.publish(
        ChatNotifyJob(
            text="normal",
            channel="tg",
            contact_id=contact_id,
            conversation_id=cid,
        )
    )
    assert jid
    assert len(mbook.list_for_conversation(conversation_id=cid)) == 1


def test_publish_chat_d22_skipped_when_contact_id_is_none(factory):
    """Task path: no contact_id → D.22 guard skipped."""
    sbook = ConversationBook(factory, settings_book=_seeded_settings_book(factory))
    mbook = MessageBook(factory)
    conv = sbook.get(sbook.add(Conversation(delivery_address='tg:1', contact_id=1, channel='tg')))
    cid = conv.id
    board = chatNotifyBoard(
        factory, messages_book=mbook, conversations_book=sbook
    )

    jid = board.publish(
        ChatNotifyJob(
            text="task fire",
            channel="task",
            contact_id=None,
            conversation_id=cid,
        )
    )
    assert jid
    assert len(mbook.list_for_conversation(conversation_id=cid)) == 1


def test_publish_chat_d22_skipped_when_no_conversations_book(factory, contact_id):
    """Backward-compat: board constructed without conversations_book → no D.22 check."""
    sbook = ConversationBook(factory, settings_book=_seeded_settings_book(factory))
    mbook = MessageBook(factory)
    conv = sbook.get(sbook.add(Conversation(delivery_address='tg:1', contact_id=contact_id, channel='tg')))
    cid = conv.id
    board = chatNotifyBoard(factory, messages_book=mbook)  # no conversations_book

    jid = board.publish(
        ChatNotifyJob(
            text="no-d22",
            channel="webui",
            contact_id=contact_id,
            conversation_id=cid,
        )
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
    from magi.bus.firmwares.jobs.chatNotifyJob import ChatNotifyJob

    sbook = ConversationBook(factory, settings_book=_seeded_settings_book(factory))
    conv = sbook.get(sbook.add(Conversation(delivery_address='tg:1', contact_id=contact_id, channel='tg')))
    cid = conv.id
    board = chatNotifyBoard(factory, conversations_book=sbook)

    job = ChatNotifyJob(
        conversation_id=cid,
        text="x",
        channel="webui",
        contact_id=contact_id,
    )

    from magi.bus.firmwares.books.local.conversationBook import ChannelMismatchError

    with pytest.raises(ChannelMismatchError):
        board.publish(job)
