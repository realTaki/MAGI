from __future__ import annotations

import asyncio

import pytest

from bus import (
    Bus,
    CallLLMJob,
    CallLLMResult,
    LLMMessage,
    LLMMessageRole,
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
                messages=[LLMMessage(role=LLMMessageRole.USER, text="hello")],
                tools=[Tool(name="echo", description="Echo input", input_schema={"type": "object"})],
            )
        )
        await asyncio.sleep(0.05)

        claimed = board.claim()
        assert claimed is not None
        assert claimed.id == job_id
        assert claimed.messages == [LLMMessage(role=LLMMessageRole.USER, text="hello")]
        assert claimed.tools == [Tool(name="echo", description="Echo input", input_schema={"type": "object"})]

        assert await board.submit_result(
            CallLLMResult(id=job_id, message=LLMMessage(role=LLMMessageRole.ASSISTANT, text="ok"))
        )
        result = board.get_result(job_id)

    assert result is not None
    assert result.message == LLMMessage(role=LLMMessageRole.ASSISTANT, text="ok")
