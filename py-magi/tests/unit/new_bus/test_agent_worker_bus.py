"""AgentWorker's durable current-BUS contract."""

from __future__ import annotations

import inspect
import time

from agent.worker import AgentWorker
from bus import (
    SYSTEM_CONTACT_ID,
    Bus,
    CallLLMJob,
    CallLLMResult,
    ChatNotify,
    CreateContactJob,
    DeliveryNotify,
    DeliveryNotifyResult,
    JobStatus,
    LLMMessage,
    LLMMessageRole,
    LLMToolCall,
    RunToolJob,
    RunToolResult,
)
from bus.firmware.books.conversationBook import ConversationBook


def _body(content: str) -> str:
    return content.split("\n", 1)[-1]


def _is_stamped(content: str, *, contact_id: int, text: str) -> bool:
    header, _, body = content.partition("\n")
    return header.startswith(f"[contact id {contact_id} | ") and body == text


def _wait_for_claim(board, *, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        claimed = board.claim()
        if claimed is not None:
            return claimed
        time.sleep(0.02)
    return None


def _conversation(bus: Bus, *, delivery_address: str = "local") -> int:
    return ConversationBook(bus._memories).add_for_channel(
        channel="test",
        delivery_address=delivery_address,
    )


def _chat(
    text: str,
    *,
    delivery_address: str = "local",
    contact_id: int = SYSTEM_CONTACT_ID,
) -> ChatNotify:
    return ChatNotify(
        publisher="test",
        channel="test",
        delivery_address=delivery_address,
        contact_id=contact_id,
        text=text,
    )


def test_agent_has_no_worker_local_concurrency_limit() -> None:
    assert "concurrency" not in inspect.signature(AgentWorker).parameters


def test_agent_worker_only_claims_and_routes_conversations() -> None:
    assert all(
        not hasattr(AgentWorker, method)
        for method in (
            "_run_turn",
            "_process",
            "_system_prompt",
            "_run_tools",
            "_call_llm",
            "_deliver",
            "_maybe_compact",
        )
    )


def test_agent_turn_flows_only_through_bus_jobs(tmp_path) -> None:
    with Bus("@agent-contract", workspace=tmp_path) as bus:
        assert bus.attach(AgentWorker)
        conversation_id = _conversation(bus)
        chat = bus.board(ChatNotify)
        llm = bus.board(CallLLMJob)
        delivery = bus.board(DeliveryNotify)
        assert chat is not None and llm is not None and delivery is not None

        chat_id = chat.publish(_chat("hello"))
        request = _wait_for_claim(llm)
        assert request is not None
        system = request.messages[0]
        assert system.role is LLMMessageRole.SYSTEM
        assert f"conversation_id: {conversation_id}" in system.content
        assert "channel: test" in system.content
        assert "delivery_address: local" in system.content
        assert "topic:" in system.content
        assert "MAGI_CONTACT_ID:" in system.content
        assert "SYSTEM_CONTACT_ID:" in system.content
        last = request.messages[-1]
        assert last.role is LLMMessageRole.USER
        assert _is_stamped(last.content, contact_id=SYSTEM_CONTACT_ID, text="hello")

        assert llm.submit_result(
                CallLLMResult(
                    id=request.id,
                    message=LLMMessage(role=LLMMessageRole.ASSISTANT, content="world"),
                )
        )
        reply = _wait_for_claim(delivery)
        assert reply is not None and reply.conversation_id == conversation_id and reply.text == "world"
        assert delivery.submit_result(DeliveryNotifyResult(id=reply.id))

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and chat.check_job_status(chat_id) is not JobStatus.COMPLETED:
            time.sleep(0.02)
        assert chat.check_job_status(chat_id) is JobStatus.COMPLETED


def test_agent_routes_claimed_turns_to_one_serial_conversation(tmp_path) -> None:
    with Bus("@agent-steering", workspace=tmp_path) as bus:
        conversation_id = _conversation(bus)
        assert bus.attach(AgentWorker)
        chat = bus.board(ChatNotify)
        llm = bus.board(CallLLMJob)
        delivery = bus.board(DeliveryNotify)
        assert chat is not None and llm is not None and delivery is not None
        first_id = chat.publish(_chat("first"))
        first = _wait_for_claim(llm)
        assert first is not None

        second_id = chat.publish(_chat("follow up"))
        assert llm.submit_result(
                CallLLMResult(
                    id=first.id,
                    message=LLMMessage(role=LLMMessageRole.ASSISTANT, content="first reply"),
                )
        )
        first_reply = _wait_for_claim(delivery)
        assert first_reply is not None and first_reply.text == "first reply"

        second = _wait_for_claim(llm)
        assert second is not None
        assert any(
            message.role is LLMMessageRole.USER
            and _is_stamped(message.content, contact_id=SYSTEM_CONTACT_ID, text="follow up")
            for message in second.messages
        )
        assert llm.submit_result(
                CallLLMResult(
                    id=second.id,
                    message=LLMMessage(role=LLMMessageRole.ASSISTANT, content="second reply"),
                )
        )

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if all(
                chat.check_job_status(job_id) is JobStatus.COMPLETED
                for job_id in (first_id, second_id)
            ):
                break
            time.sleep(0.02)
        assert chat.check_job_status(first_id) is JobStatus.COMPLETED
        assert chat.check_job_status(second_id) is JobStatus.COMPLETED


def test_agent_routes_different_conversations_independently(tmp_path) -> None:
    with Bus("@agent-conversations", workspace=tmp_path) as bus:
        assert bus.attach(AgentWorker)
        first_conversation = _conversation(bus, delivery_address="one")
        second_conversation = _conversation(bus, delivery_address="two")
        assert first_conversation != second_conversation
        chat = bus.board(ChatNotify)
        llm = bus.board(CallLLMJob)
        delivery = bus.board(DeliveryNotify)
        assert chat is not None and llm is not None and delivery is not None
        chat.publish(_chat("first", delivery_address="one"))
        chat.publish(_chat("second", delivery_address="two"))

        first, second = _wait_for_claim(llm), _wait_for_claim(llm)
        assert first is not None and second is not None
        assert {_body(request.messages[-1].content) for request in (first, second)} == {
            "first",
            "second",
        }
        for request in (first, second):
            assert llm.submit_result(
                    CallLLMResult(
                        id=request.id,
                        message=LLMMessage(
                            role=LLMMessageRole.ASSISTANT,
                            content=f"reply: {_body(request.messages[-1].content)}",
                        ),
                    )
            )
        replies = [_wait_for_claim(delivery), _wait_for_claim(delivery)]
        assert {reply.text for reply in replies if reply is not None} == {
            "reply: first",
            "reply: second",
        }


def test_agent_tool_loop_returns_tool_result_to_the_next_llm_call(tmp_path) -> None:
    with Bus("@agent-tools", workspace=tmp_path) as bus:
        assert bus.attach(AgentWorker)
        conversation_id = _conversation(bus)
        chat = bus.board(ChatNotify)
        llm = bus.board(CallLLMJob)
        tools = bus.board(RunToolJob)
        delivery = bus.board(DeliveryNotify)
        assert chat is not None and llm is not None and tools is not None and delivery is not None

        chat.publish(_chat("look it up"))
        first = _wait_for_claim(llm)
        assert first is not None
        assert llm.submit_result(
                CallLLMResult(
                    id=first.id,
                    message=LLMMessage(
                        role=LLMMessageRole.ASSISTANT,
                        content="Looking it up.",
                        tool_calls=[
                            LLMToolCall(
                                tool_call_id="call-1", name="lookup", arguments={"q": "MAGI"}
                            )
                        ],
                    ),
                )
        )
        progress = _wait_for_claim(delivery)
        assert progress is not None and progress.text == "Looking it up."
        tool = _wait_for_claim(tools)
        assert tool is not None and tool.call.tool_call_id == "call-1"
        assert tools.submit_result(RunToolResult(id=tool.id, content="found it"))

        second = _wait_for_claim(llm)
        assert second is not None
        looking = [
            message
            for message in second.messages
            if message.role is LLMMessageRole.ASSISTANT and message.content == "Looking it up."
        ]
        assert len(looking) == 1
        assert looking[0].tool_calls
        assert second.messages[-1] == LLMMessage(
            role=LLMMessageRole.TOOL, tool_call_id="call-1", content="found it"
        )
        assert llm.submit_result(
                CallLLMResult(
                    id=second.id,
                    message=LLMMessage(role=LLMMessageRole.ASSISTANT, content="Here it is."),
                )
        )
        reply = _wait_for_claim(delivery)
        assert reply is not None and reply.text == "Here it is."


def test_agent_tool_failure_returns_as_tool_result_not_delivery(tmp_path) -> None:
    with Bus("@agent-tool-error", workspace=tmp_path) as bus:
        assert bus.attach(AgentWorker)
        conversation_id = _conversation(bus)
        chat = bus.board(ChatNotify)
        llm = bus.board(CallLLMJob)
        tools = bus.board(RunToolJob)
        delivery = bus.board(DeliveryNotify)
        assert chat is not None and llm is not None and tools is not None and delivery is not None

        chat.publish(_chat("look it up"))
        first = _wait_for_claim(llm)
        assert first is not None
        assert llm.submit_result(
            CallLLMResult(
                id=first.id,
                message=LLMMessage(
                    role=LLMMessageRole.ASSISTANT,
                    content="Looking it up.",
                    tool_calls=[
                        LLMToolCall(
                            tool_call_id="call-1", name="lookup", arguments={"q": "MAGI"}
                        )
                    ],
                ),
            )
        )
        progress = _wait_for_claim(delivery)
        assert progress is not None and progress.text == "Looking it up."
        tool = _wait_for_claim(tools)
        assert tool is not None
        assert tools.submit_result(
            RunToolResult(id=tool.id, status=JobStatus.FAILED, error="lookup failed")
        )

        second = _wait_for_claim(llm)
        assert second is not None
        assert second.messages[-1] == LLMMessage(
            role=LLMMessageRole.TOOL,
            tool_call_id="call-1",
            content="lookup failed",
            is_error=True,
        )
        assert llm.submit_result(
            CallLLMResult(
                id=second.id,
                message=LLMMessage(role=LLMMessageRole.ASSISTANT, content="It failed."),
            )
        )
        reply = _wait_for_claim(delivery)
        assert reply is not None and reply.text == "It failed."


def test_agent_stamps_distinct_contact_ids_on_user_messages(tmp_path) -> None:
    with Bus("@agent-speakers", workspace=tmp_path) as bus:
        assert bus.attach(AgentWorker)
        conversation_id = _conversation(bus)
        contacts = bus.board(CreateContactJob)
        chat = bus.board(ChatNotify)
        llm = bus.board(CallLLMJob)
        delivery = bus.board(DeliveryNotify)
        assert contacts is not None and chat is not None and llm is not None and delivery is not None
        alice = contacts.publish(CreateContactJob(publisher="test", name="alice")).contact_id
        bob = contacts.publish(CreateContactJob(publisher="test", name="bob")).contact_id
        assert alice is not None and bob is not None

        chat.publish(_chat("hi", contact_id=alice))
        first = _wait_for_claim(llm)
        assert first is not None
        assert _is_stamped(first.messages[-1].content, contact_id=alice, text="hi")
        assert llm.submit_result(
            CallLLMResult(
                id=first.id,
                message=LLMMessage(role=LLMMessageRole.ASSISTANT, content="hello alice"),
            )
        )
        assert _wait_for_claim(delivery) is not None

        chat.publish(_chat("yo", contact_id=bob))
        second = _wait_for_claim(llm)
        assert second is not None
        assert any(
            message.role is LLMMessageRole.USER
            and _is_stamped(message.content, contact_id=alice, text="hi")
            for message in second.messages
        )
        assert second.messages[-1].role is LLMMessageRole.USER
        assert _is_stamped(second.messages[-1].content, contact_id=bob, text="yo")
