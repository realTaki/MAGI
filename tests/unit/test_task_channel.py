"""Unit tests for the scheduled-task channel boundary."""

from __future__ import annotations

import pytest

from magi.channels import Channel
from magi.channels.tasks.channel import TaskChannel


def test_task_channel_has_the_internal_scheduled_identifier() -> None:
    assert TaskChannel.identifier is Channel.SCHEDULED


@pytest.mark.asyncio
async def test_task_channel_dispatches_to_the_task_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi.channels.tasks import runner

    received: dict[str, object] = {}

    async def fake_execute_task(
        task_id: str,
        *,
        manual: bool = False,
        pre_created_run_id: str | None = None,
    ) -> None:
        received.update(
            task_id=task_id,
            manual=manual,
            pre_created_run_id=pre_created_run_id,
        )

    monkeypatch.setattr(runner, "execute_task", fake_execute_task)
    await TaskChannel.dispatch("/state", "task-1", manual=True, pre_created_run_id="run-1")

    assert received == {
        "task_id": "task-1",
        "manual": True,
        "pre_created_run_id": "run-1",
    }
