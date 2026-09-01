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

        soul_id = prompts.publish(GetPromptJob(publisher="test", key="agent/soul"))
        soul = prompts.get_result(soul_id)
        assert soul is not None and soul.value and "MAGI Soul" in soul.value
