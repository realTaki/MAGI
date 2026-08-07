"""Background-shell termination — :class:`BashKillTool`.

Graceful SIGTERM first (5 s grace), then SIGKILL if the
process refuses to exit. Cleans up the monitor task and
the registry entry so the background-state dict doesn't
grow without bound.
"""

from __future__ import annotations

import logging
from typing import Any

from magi.tools.base import Tool, ToolContext, ToolResult
from magi.tools.shell._manager import _BackgroundShellManager


logger = logging.getLogger("magi.tools.shell.kill")


class BashKillTool(Tool):
    """Terminate a background bash shell."""

    name = "bash_kill"

    # Visible only to ``admin`` and ``assigned``
    # operators — same gate as the WebUI dashboard and
    # as :class:`~magi.tools.tasks.schedule.ScheduleTaskTool`
    # / the action-item trio. The chat path always
    # passes the operator's role through to
    # ``handle_message(caller_role=...)`` so non-eligible
    # callers never see these tools in the LLM's menu.
    # ``MCPTool`` is intentionally permissive
    # (operator-configured at the MCP server level).
    ALLOWED_ROLES = frozenset({"assigned"})

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
        _ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        bash_id = (kwargs.get("bash_id") or "").strip()
        if not bash_id:
            return ToolResult(content="bash_id is required", is_error=True)

        # Drain any unread output before terminating so
        # the LLM sees what the process wrote just
        # before it died.
        shell = _BackgroundShellManager.get(bash_id)
        if shell is not None:
            tail = "\n".join(shell.get_new_output())
        else:
            tail = ""

        try:
            await _BackgroundShellManager.terminate(bash_id)
        except ValueError as e:
            # Not in the registry — already terminated
            # or never existed. The LLM may have
            # retried; treat as a successful no-op.
            available = _BackgroundShellManager.list_ids()
            return ToolResult(
                content=(
                    f"{e}. Available: {available or 'none'}. "
                    "(idempotent — already gone or never "
                    "registered; nothing to kill.)"
                ),
                is_error=True,
            )

        body = "Killed."
        if tail:
            body = f"Last output before kill:\n{tail}\n{body}"
        return ToolResult(content=body)


__all__ = ["BashKillTool"]