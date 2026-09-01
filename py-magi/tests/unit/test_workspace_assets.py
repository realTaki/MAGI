"""Agent prompt assets are registered through Firmware Jobs on attach."""

from __future__ import annotations

import pytest

from agent.prompt_defaults import prompt_defaults
from agent.worker import AgentWorker
from bus import Bus, GetPromptJob


@pytest.mark.asyncio
async def test_agent_prompt_assets_are_bus_owned(tmp_path) -> None:
    expected = {key for key, _ in prompt_defaults()}
    with Bus("@agent-assets", workspace=tmp_path) as bus:
        assert bus.attach(AgentWorker)
        board = bus.board(GetPromptJob)
        assert board is not None
        for key in expected:
            result = board.get_result(board.publish(GetPromptJob(publisher="test", key=key)))
            assert result is not None and result.value
