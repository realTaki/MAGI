"""The internal channel adapter for a scheduled task fire.

[plan amendment §11]: ``TaskChannel.dispatch`` is now a deprecated wrapper
that forwards to ``bus.run_task_job_board.publish(RunTaskJob(...))``.
The TaskWorker claims and executes via ``_fire_task``.
"""

from __future__ import annotations

import logging
from typing import Final

from magi.channels import Channel, get_current_new_bus

logger = logging.getLogger("magi.channels.tasks.channel")


class TaskChannel:
    """Dispatch scheduled-task invocations into the agent runtime.

    Deprecated wrapper — publishes a ``RunTaskJob`` to the new_bus.
    TaskWorker claims and executes.
    """

    identifier: Final[Channel] = Channel.SCHEDULED

    @classmethod
    async def dispatch(
        cls,
        task_id: str,
        *,
        manual: bool = False,
        pre_created_run_id: str | None = None,
    ) -> None:
        """Publish a RunTaskJob to new_bus.

        The TaskWorker claims it and calls ``_fire_task``.
        Falls back to no-op if new_bus isn't available.
        """
        bus = get_current_new_bus()
        if bus is None:
            logger.warning(
                "TaskChannel.dispatch(%s): new_bus not available; task not fired",
                task_id,
            )
            return

        from magi.new_bus.guild.runTaskJob import RunTaskJob

        fired_by = "manual_run" if manual else "api_manual_run"
        bus.run_task_job_board.publish(RunTaskJob(
            task_id=task_id,
            manual=manual,
            fired_by=fired_by,
        ))
        logger.info(
            "TaskChannel.dispatch: published RunTaskJob for task %s (manual=%s)",
            task_id, manual,
        )
