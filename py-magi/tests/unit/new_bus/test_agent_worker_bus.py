"""AgentWorker's durable current-BUS contract."""

from __future__ import annotations

import inspect
import time

from agent.worker import AgentWorker
from bus import (
    Bus,
    CallLLMJob,
    CallLLMResult,
    ChatNotify,
    CreateConversationJob,
    DeliveryNotify,
    DeliveryNotifyResult,
    JobStatus,
    LLMMessage,
    LLMMessageRole,
    LLMToolCall,
    RunToolJob,
    RunToolResult,
)


def _wait_for_claim(board, *, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        claimed = board.claim()
        if claimed is not None:
            return claimed
        time.sleep(0.02)
    return None


def _conversation(bus: Bus) -> int:
    board = bus.board(CreateConversationJob)
    assert board is not None
    job_id = board.publish(
        CreateConversationJob(
            publisher="test", delivery_address="local", channel="test", topic="test"
        )
    )
    result = board.get_result(job_id)
    assert result is not None and result.conversation_id is not None
    return result.conversation_id


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

        chat_id = chat.publish(
            ChatNotify(publisher="test", conversation_id=conversation_id, text="hello")
        )
        request = _wait_for_claim(llm)
        assert request is not None
        assert request.messages[-1] == LLMMessage(role=LLMMessageRole.USER, content="hello")

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
        first_id = chat.publish(
            ChatNotify(publisher="test", conversation_id=conversation_id, text="first")
        )
        first = _wait_for_claim(llm)
        assert first is not None

        second_id = chat.publish(
            ChatNotify(publisher="test", conversation_id=conversation_id, text="follow up")
        )
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
        assert LLMMessage(role=LLMMessageRole.USER, content="follow up") in second.messages
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
        first_conversation, second_conversation = _conversation(bus), _conversation(bus)
        chat = bus.board(ChatNotify)
        llm = bus.board(CallLLMJob)
        delivery = bus.board(DeliveryNotify)
        assert chat is not None and llm is not None and delivery is not None
        chat.publish(ChatNotify(publisher="test", conversation_id=first_conversation, text="first"))
        chat.publish(ChatNotify(publisher="test", conversation_id=second_conversation, text="second"))

        first, second = _wait_for_claim(llm), _wait_for_claim(llm)
        assert first is not None and second is not None
        assert {request.messages[-1].content for request in (first, second)} == {"first", "second"}
        for request in (first, second):
            assert llm.submit_result(
                    CallLLMResult(
                        id=request.id,
                        message=LLMMessage(
                            role=LLMMessageRole.ASSISTANT,
                            content=f"reply: {request.messages[-1].content}",
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

        chat.publish(ChatNotify(publisher="test", conversation_id=conversation_id, text="look it up"))
        first = _wait_for_claim(llm)
        assert first is not None
        assert llm.submit_result(
                CallLLMResult(
                    id=first.id,
                    message=LLMMessage(
                        role=LLMMessageRole.ASSISTANT,
                        content="",
                        tool_calls=[
                            LLMToolCall(
                                tool_call_id="call-1", name="lookup", arguments={"q": "MAGI"}
                            )
                        ],
                    ),
                )
        )
        tool = _wait_for_claim(tools)
        assert tool is not None and tool.call.tool_call_id == "call-1"
        assert tools.submit_result(RunToolResult(id=tool.id, content="found it"))

        second = _wait_for_claim(llm)
        assert second is not None
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
