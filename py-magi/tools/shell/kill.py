"""Background-shell termination — :class:`BashKillTool`.

Cancels the monitor task, drains any remaining stdout,
then sends SIGTERM (5 s grace) → SIGKILL if the process
refuses to die.  Removes the shell from the registry.

Idempotent: a second call on an already-terminated shell
is a successful no-op — the LLM can retry without seeing
a false ``is_error``.
"""

from __future__ import annotations

from typing import Any

from tools.base import Tool, ToolContext, ToolResult
from tools.shell._manager import _BackgroundShellManager


class BashKillTool(Tool):
    """Terminate a background bash shell."""

    name = "bash_kill"

    # Visible only to ``admin`` and ``assigned``
    # operators — same gate as the WebUI dashboard and
    # as :class:`~tools.tasks.schedule.ScheduleTaskTool`
    # / the action-item trio.
    ALLOWED_ROLES = frozenset({"admin", "assigned"})

    description = (
        "Terminate a background bash shell by id. "
        "Sends SIGTERM first; if the process doesn't "
        "exit within 5 s, sends SIGKILL. Cleans up the "
        "monitor task and removes the shell from the "
        "background registry.\n\n"
        "Use this when a long-running shell started with "
        "``run_in_background=true`` needs to stop."
    )

    input_schema = {
        "type": "object",
        "properties": {
            "bash_id": {
                "type": "string",
                "description": (
                    "The id of the background shell to "
                    "terminate. Returned by ``bash`` when "
                    "``run_in_background=true``."
                ),
            },
        },
        "required": ["bash_id"],
    }

    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        _ = ctx
        bash_id = (kwargs.get("bash_id") or "").strip()
        if not bash_id:
            return ToolResult(content="bash_id is required", is_error=True)

        shell = _BackgroundShellManager.get(bash_id)
        if shell is None:
            available = _BackgroundShellManager.list_ids()
            return ToolResult(
                content=(
                    f"Shell not found: {bash_id}. "
                    f"Available: {available or 'none'}. "
                    "(idempotent — already gone or never "
                    "registered; nothing to kill.)"
                ),
                is_error=True,
            )

        # Drain the tail before termination so the LLM
        # sees what the process wrote just before it died.
        tail = "\n".join(shell.get_new_output())

        try:
            await _BackgroundShellManager.terminate(bash_id=bash_id)
        except ValueError as e:
            # The monitor task already finished and popped
            # the entry — the shell is effectively gone.
            available = _BackgroundShellManager.list_ids()
            return ToolResult(
                content=(
                    f"{e}. Available: {available or 'none'}. "
                    "(idempotent — already gone; nothing to "
                    "kill.)"
                ),
                is_error=True,
            )

        body = "Killed."
        if tail:
            body = f"Last output before kill:\n{tail}\n{body}"
        return ToolResult(content=body)


__all__ = ["BashKillTool"]
