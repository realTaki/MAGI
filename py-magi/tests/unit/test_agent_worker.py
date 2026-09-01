"""Focused AgentWorker behaviour on the public BUS contract."""

from __future__ import annotations

import pytest

from agent.worker import AgentWorker
from bus import Bus, GetPromptJob, GetSettingJob


@pytest.mark.asyncio
async def test_agent_attach_registers_prompts_and_defaults(tmp_path) -> None:
    with Bus("@agent-defaults", workspace=tmp_path) as bus:
        assert bus.attach(AgentWorker)
        prompts = bus.board(GetPromptJob)
        settings = bus.board(GetSettingJob)
        assert prompts is not None and settings is not None

        soul_id = prompts.publish(GetPromptJob(publisher="test", key="agent/soul"))
        soul = prompts.get_result(soul_id)
        assert soul is not None and soul.value and "MAGI Soul" in soul.value

        iterations_id = settings.publish(
            GetSettingJob(publisher="test", key="agent.max_iterations")
        )
        iterations = settings.get_result(iterations_id)
        assert iterations is not None and iterations.value == "10"
