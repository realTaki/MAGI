"""Background-shell output polling — :class:`BashOutputTool`.

Jobs: none. Reads the in-process ``_BackgroundShellManager``.

Returns only lines accumulated since the last poll
against the same ``bash_id`` — the LLM doesn't have to
track what it has already read. An optional regex
``filter_str`` narrows to matching lines (handy for
tailing a specific log line in a busy process).
"""

from __future__ import annotations

from typing import Any

from tools.BaseTool import BaseTool, ToolResult
from tools.shell._manager import _BackgroundShellManager


class BashOutputTool(BaseTool):
    """Retrieve new output from a background shell."""

    name = "bash_output"

    description = (
        "Retrieve new output from a background bash shell. "
        "Returns only stdout accumulated since the last "
        "call against the same ``bash_id`` (stderr is "
        "merged into stdout for background shells).\n\n"
        "Optional ``filter_str`` is a regex — only lines "
        "matching it are returned; non-matching lines are "
        "consumed (skipped permanently so the same line "
        "isn't returned twice if you later drop the "
        "filter).\n\n"
        "Use this when monitoring a long-running shell "
        "started with ``run_in_background=true``."
    )

    input_schema = {
        "type": "object",
        "properties": {
            "bash_id": {
                "type": "string",
                "description": (
                    "The id of the background shell to read "
                    "from. Returned by ``bash`` when "
                    "``run_in_background=true``."
                ),
            },
            "filter_str": {
                "type": "string",
                "description": (
                    "Optional regex. Only matching lines are "
                    "returned; non-matching lines are "
                    "consumed. Invalid regex is treated as "
                    '"no filter".'
                ),
            },
        },
        "required": ["bash_id"],
    }

    async def run(
        self,
        **kwargs: Any,
    ) -> ToolResult:
        bash_id = (kwargs.get("bash_id") or "").strip()
        if not bash_id:
            return ToolResult(content="bash_id is required", is_error=True)
        filter_str = kwargs.get("filter_str")

        shell = _BackgroundShellManager.get(bash_id)
        if shell is None:
            available = _BackgroundShellManager.list_ids()
            return ToolResult(
                content=(f"Shell not found: {bash_id}. Available: {available or 'none'}."),
                is_error=True,
            )

        new_lines = shell.get_new_output(filter_pattern=filter_str)
        stdout = "\n".join(new_lines)
        # Surface status so the LLM knows whether to
        # expect more output or finish polling.
        exit_str = f" exit={shell.exit_code}" if shell.exit_code is not None else ""
        # A chatty process can overrun the per-shell buffer. Say so
        # explicitly — a silent hole in the output would read as
        # "the process printed nothing there".
        dropped_str = f" dropped={shell.dropped_lines}" if shell.dropped_lines else ""
        suffix = f"[status] {shell.status}{exit_str}{dropped_str}"

        if not stdout:
            return ToolResult(
                content=f"(no new output)\n{suffix}",
            )
        return ToolResult(
            content=f"{stdout}\n{suffix}",
        )


__all__ = ["BashOutputTool"]
