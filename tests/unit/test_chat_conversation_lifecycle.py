"""Regression coverage for WebUI conversation creation and transcript paging."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from magi.old_bus.bases.db import EngineFactory
from magi.old_bus.firmwares.books.local import Conversation, ConversationBook, Message, MessageBook
from magi.channels.api.chat import ChatSendRequest, send_chat
from magi.channels.api.chat_conversations import create_conversation


@pytest.mark.asyncio
async def test_first_webui_send_uses_generated_conversation_id(monkeypatch) -> None:
    """The new chat job must not carry the DTO's default id (zero)."""
    bus = MagicMock()
    bus.contacts_book.get.return_value = SimpleNamespace(id=17, tgid=None)
    bus.conversations_book.add.return_value = 41
    bus.agent_job_board.publish.return_value = 73
    monkeypatch.setattr(
        "magi.channels.api.auth_gates._proxy_identity", lambda _request: (17, True)
    )

    result = await send_chat(
        ChatSendRequest(text="hello", conversation_id=None),
        SimpleNamespace(cookies={}),
        None,
        bus,
    )

    assert result.conversation_id == 41
    published = bus.agent_job_board.publish.call_args.args[0]
    assert published.conversation_id == 41
    assert published.contact_id == 17
    assert published.channel == "webui"


def test_explicit_conversation_create_returns_generated_id(monkeypatch) -> None:
    bus = MagicMock()
    bus.contacts_book.get.return_value = SimpleNamespace(id=17, tgid=None)
    bus.conversations_book.add.return_value = 41
    monkeypatch.setattr(
        "magi.channels.api.auth_gates._proxy_identity", lambda _request: (17, True)
    )

    request = SimpleNamespace(
        cookies={}, app=SimpleNamespace(state=SimpleNamespace(bus=bus))
    )
    result = create_conversation(request, None, bus)

    assert result.conversation_id == 41


def test_message_page_starts_at_newest_tail_then_returns_chronological_order() -> None:
    factory = EngineFactory("sqlite:///:memory:")
    factory.create_all()
    conversations = ConversationBook(factory)
    messages = MessageBook(factory)
    contact_id = 17
    conversation_id = conversations.add(
        Conversation(delivery_address="", contact_id=contact_id, channel="webui")
    )
    for number in range(5):
        messages.add(Message(conversation_id=conversation_id, role="user", text=str(number)))

    newest, total_active, total_all = conversations.get_messages_page(
        contact_id, conversation_id, limit=2, offset=0
    )
    older, _, _ = conversations.get_messages_page(
        contact_id, conversation_id, limit=2, offset=2
    )

    assert [message.text for message in newest] == ["3", "4"]
    assert [message.text for message in older] == ["1", "2"]
    assert (total_active, total_all) == (5, 5)
