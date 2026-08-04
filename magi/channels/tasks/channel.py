"""The internal channel adapter for a scheduled task fire.

The scheduler owns *when* a task fires.  :class:`TaskChannel` owns the
channel boundary: a fire is an inbound invocation that enters the same MAGI
agent loop used by other channels.  Keeping this small adapter explicit makes
future trigger sources (event, webhook, or system-proactive) use the same
execution contract without making the scheduler itself an agent runtime.
"""

from __future__ import annotations

from typing import Final

from magi.channels import Channel


class TaskChannel:
    """Dispatch scheduled-task invocations into the agent runtime."""

    identifier: Final[Channel] = Channel.SCHEDULED

    @classmethod
    async def dispatch(
        cls,
        task_id: str,
        *,
        manual: bool = False,
        pre_created_run_id: str | None = None,
    ) -> None:
        """Execute one persisted task fire.

        Import lazily so merely importing channel metadata does not pull the
        agent loop, SQLAlchemy models, or runtime provider dependencies into
        lightweight callers.
        """
        from magi.channels.tasks.runner import execute_task

        await execute_task(
            task_id,
            manual=manual,
            pre_created_run_id=pre_created_run_id,
        )
