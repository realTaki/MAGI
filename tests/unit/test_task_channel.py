"""Unit tests for TaskChannel — rebased to RunTaskJob publish flow."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from magi.channels import Channel
from magi.channels.tasks.channel import TaskChannel


def test_task_channel_has_the_internal_scheduled_identifier() -> None:
    assert TaskChannel.identifier is Channel.SCHEDULED


@pytest.mark.asyncio
async def test_task_channel_dispatches_publishes_run_task_job(monkeypatch):
    """TaskChannel.dispatch publishes a RunTaskJob through bus."""
    from magi.channels import set_current_bus

    mock_bus = MagicMock()
    mock_bus.run_task_job_board.publish = MagicMock(return_value="jid_42")
    set_current_bus(mock_bus)

    try:
        await TaskChannel.dispatch(
            "task_abc", manual=True,
        )
    finally:
        set_current_bus(None)

    mock_bus.run_task_job_board.publish.assert_called_once()
    call_args = mock_bus.run_task_job_board.publish.call_args
    job = call_args[0][0]
    assert job.task_id == "task_abc"
    assert job.manual is True


@pytest.mark.asyncio
async def test_task_channel_dispatch_noops_without_bus():
    """When bus is not set, dispatch should log and return without error."""
    from magi.channels import set_current_bus

    # Ensure no bus is set
    set_current_bus(None)

    # Should not raise — just logs warning
    await TaskChannel.dispatch("task_missing", manual=False)
