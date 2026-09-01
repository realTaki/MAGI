from __future__ import annotations

import asyncio
from dataclasses import asdict

import pytest

from bus import (
    Bus,
    CallLLMJob,
    CallLLMResult,
    ListToolsJob,
    LLMMessage,
    LLMMessageRole,
    LLMTool,
    LLMToolCall,
    RunToolJob,
    SetToolsJob,
    Tool,
)


@pytest.mark.asyncio
async def test_call_llm_job_round_trips_typed_contract_through_job_board(tmp_path) -> None:
    with Bus("@llm-contract", workspace=tmp_path) as bus:
        board = bus.board(CallLLMJob)
        assert board is not None
        job_id = board.publish(
            CallLLMJob(
                publisher="test",
                messages=[LLMMessage(role=LLMMessageRole.USER, content="hello")],
                tools=[LLMTool(name="echo", description="Echo input", input_schema={"type": "object"})],
            )
        )
        await asyncio.sleep(0.05)

        claimed = board.claim()
        assert claimed is not None
        assert claimed.id == job_id
        assert claimed.messages == [LLMMessage(role=LLMMessageRole.USER, content="hello")]
        assert claimed.tools == [LLMTool(name="echo", description="Echo input", input_schema={"type": "object"})]

        assert await board.submit_result(
            CallLLMResult(id=job_id, message=LLMMessage(role=LLMMessageRole.ASSISTANT, content="ok"))
        )
        result = board.get_result(job_id)

    assert result is not None
    assert result.message == LLMMessage(role=LLMMessageRole.ASSISTANT, content="ok")


@pytest.mark.asyncio
async def test_tool_catalog_and_execution_job_wrap_pure_llm_values(tmp_path) -> None:
    definition = LLMTool(name="echo", description="Echo input", input_schema={"type": "object"})
    catalog_tool = Tool(name=definition.name, definition=definition)
    assert Tool.parse(asdict(catalog_tool)).definition == definition

    call = LLMToolCall(tool_call_id="call-1", name="echo", arguments={"text": "hello"})
    with Bus("@llm-tool-wrapper", workspace=tmp_path) as bus:
        set_tools = bus.board(SetToolsJob)
        list_tools = bus.board(ListToolsJob)
        board = bus.board(RunToolJob)
        assert set_tools is not None
        assert list_tools is not None
        assert board is not None
        set_id = set_tools.publish(
            SetToolsJob(
                publisher="test",
                tools=[catalog_tool],
            )
        )
        job_id = board.publish(RunToolJob(publisher="test", call=call))
        await asyncio.sleep(0.05)
        assert set_tools.get_result(set_id) is not None
        listed_id = list_tools.publish(ListToolsJob(publisher="test"))
        await asyncio.sleep(0.05)
        listed = list_tools.get_result(listed_id)
        claimed = board.claim()

    assert listed is not None
    assert listed.tools is not None
    assert [tool.definition for tool in listed.tools] == [definition]
    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.call == call
