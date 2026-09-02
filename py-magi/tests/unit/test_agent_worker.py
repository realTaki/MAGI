"""Focused AgentWorker behaviour on the public BUS contract."""

from __future__ import annotations

import pytest

from agent.worker import AgentWorker
from bus import Bus, GetPromptJob


@pytest.mark.asyncio
async def test_agent_attach_registers_prompts_and_defaults(tmp_path) -> None:
    with Bus("@agent-defaults", workspace=tmp_path) as bus:
        assert bus.attach(AgentWorker)
        prompts = bus.board(GetPromptJob)
        assert prompts is not None

        prompt = prompts.publish(GetPromptJob(publisher="test", key="agent/AGENT"))
        assert prompt.value and "MAGI" in prompt.value
