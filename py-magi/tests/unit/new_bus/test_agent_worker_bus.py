"""AgentWorker's durable current-BUS contract."""

from __future__ import annotations

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
from bus.base.go import go


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

        assert go(
            llm.submit_result(
                CallLLMResult(
                    id=request.id,
                    message=LLMMessage(role=LLMMessageRole.ASSISTANT, content="world"),
                )
            )
        ).result(timeout=5)
        reply = _wait_for_claim(delivery)
        assert reply is not None and reply.conversation_id == conversation_id and reply.text == "world"
        assert go(delivery.submit_result(DeliveryNotifyResult(id=reply.id))).result(timeout=5)

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and chat.check_job_status(chat_id) is not JobStatus.COMPLETED:
            time.sleep(0.02)
        assert chat.check_job_status(chat_id) is JobStatus.COMPLETED


def test_chat_board_reserves_active_conversations_for_steering(tmp_path) -> None:
    with Bus("@agent-steering", workspace=tmp_path) as bus:
        first, second = _conversation(bus), _conversation(bus)
        chat = bus.board(ChatNotify)
        assert chat is not None
        chat.publish(ChatNotify(publisher="test", conversation_id=first, text="first"))
        chat.publish(ChatNotify(publisher="test", conversation_id=first, text="follow up"))
        chat.publish(ChatNotify(publisher="test", conversation_id=second, text="second"))
        time.sleep(0.1)

        initial = chat.claim_for_new_conversation(active_conversation_ids=set())
        assert initial is not None and initial.conversation_id == first
        concurrent = chat.claim_for_new_conversation(active_conversation_ids={first})
        assert concurrent is not None and concurrent.conversation_id == second
        steering = chat.claim_for_steering(conversation_id=first)
        assert steering is not None and steering.text == "follow up"


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
        assert go(
            llm.submit_result(
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
        ).result(timeout=5)
        tool = _wait_for_claim(tools)
        assert tool is not None and tool.call.tool_call_id == "call-1"
        assert go(tools.submit_result(RunToolResult(id=tool.id, content="found it"))).result(timeout=5)

        second = _wait_for_claim(llm)
        assert second is not None
        assert second.messages[-1] == LLMMessage(
            role=LLMMessageRole.TOOL, tool_call_id="call-1", content="found it"
        )
        assert go(
            llm.submit_result(
                CallLLMResult(
                    id=second.id,
                    message=LLMMessage(role=LLMMessageRole.ASSISTANT, content="Here it is."),
                )
            )
        ).result(timeout=5)
        reply = _wait_for_claim(delivery)
        assert reply is not None and reply.text == "Here it is."
