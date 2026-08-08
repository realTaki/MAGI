"""Background-shell output polling — :class:`BashOutputTool`.

Two read paths, picked automatically:

- **Same worker** — the shell's monitor task lives in this
  process; we drain the in-memory ``output_lines`` ring buffer.
  No DB round-trip.
- **Cross worker** — the shell was spawned by a sibling
  worker process; we read lines from
  :class:`~magi.new_bus.library.local.shellBook.BackgroundShellLineBook`.
  Per-shell ``consumed_seq`` cursor on this worker keeps a
  follow-up call returning only newer output (same contract
  as the in-memory ``last_read_index``).

``filter_str`` is a regex; non-matching lines are **consumed**
(skipped permanently) regardless of which read path served
them — dropping the filter later doesn't replay the filtered
lines.

A single ``[status] running|completed|…`` suffix surfaces the
shell's terminal state so the LLM knows whether to keep
polling or wrap up.
"""

from __future__ import annotations

import logging
from typing import Any

from magi.tools.base import Tool, ToolContext, ToolResult
from magi.tools.shell._manager import (
    _BackgroundShell,
    _BackgroundShellManager,
    _CrossWorkerView,
)


logger = logging.getLogger("magi.tools.shell.output")


class BashOutputTool(Tool):
    """Retrieve new output from a background shell."""

    name = "bash_output"

    # Visible only to ``admin`` and ``assigned``
    # operators — same gate as the WebUI dashboard and
    # as :class:`~magi.tools.tasks.schedule.ScheduleTaskTool`
    # / the action-item trio. The chat path always
    # passes the operator's role through to
    # ``handle_message(caller_role=...)`` so non-eligible
    # callers never see these tools in the LLM's menu.
    # ``MCPTool`` is intentionally permissive
    # (operator-configured at the MCP server level).
    ALLOWED_ROLES = frozenset({"admin", "assigned"})

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
                    "\"no filter\"."
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
        bash_id = (kwargs.get("bash_id") or "").strip()
        if not bash_id:
            return ToolResult(content="bash_id is required", is_error=True)
        filter_str = kwargs.get("filter_str")

        # Local fast path — same worker that spawned the
        # shell. Skips DB entirely.
        local = _BackgroundShellManager.get(bash_id)
        if isinstance(local, _BackgroundShell):
            return self._format_local(local, filter_str)

        # Cross-worker path — shell lives in another
        # process. Read from the line Book; advance the
        # per-worker consumed-seq cursor.
        if ctx.bus is not None:
            cross = _BackgroundShellManager.view(
                bash_id=bash_id, bus=ctx.bus,
            )
            if isinstance(cross, _CrossWorkerView):
                return await self._format_cross(
                    bash_id=bash_id,
                    view=cross,
                    filter_str=filter_str,
                    bus=ctx.bus,
                )

        # Neither local nor in DB.
        available = _BackgroundShellManager.list_ids(bus=ctx.bus)
        return ToolResult(
            content=(
                f"Shell not found: {bash_id}. "
                f"Available: {available or 'none'}."
            ),
            is_error=True,
        )

    # -- helpers ----------------------------------------------------------

    def _format_local(
        self,
        shell: _BackgroundShell,
        filter_str: str | None,
    ) -> ToolResult:
        """Same-worker read — drain the in-memory ring buffer."""
        new_lines = shell.get_new_output(filter_pattern=filter_str)
        stdout = "\n".join(new_lines)
        suffix = self._status_suffix(
            status=shell.status,
            exit_code=shell.exit_code,
        )
        if not stdout:
            return ToolResult(content=f"(no new output)\n{suffix}")
        return ToolResult(content=f"{stdout}\n{suffix}")

    async def _format_cross(
        self,
        *,
        bash_id: str,
        view: _CrossWorkerView,
        filter_str: str | None,
        bus: Any,
    ) -> ToolResult:
        """Cross-worker read — fetch new lines from the line Book.

        The cursor is per-worker (``_consumed_seq``); a sibling
        worker that polls the same shell sees its own view of
        what's new. Mirrors the same-worker "advance on read"
        contract so the LLM's follow-up calls don't replay
        already-seen output.
        """
        after = _BackgroundShellManager.consumed_seq(bash_id)
        lines_book = bus.background_shell_lines_book
        rows = lines_book.list_after(
            bash_id=bash_id, after_seq=after, limit=1000,
        )
        # Apply filter the same way as the in-memory path —
        # non-matching lines still advance the cursor so they
        # don't replay on a later unfiltered poll. Validate
        # the regex once; on ``re.error`` we treat it as "no
        # filter" rather than losing lines to a typo.
        new_lines: list[str] = []
        advance_to = after
        pattern = None
        if filter_str:
            try:
                import re
                pattern = re.compile(filter_str)
            except re.error:
                pattern = None
        for row in rows:
            advance_to = max(advance_to, row.seq)
            if pattern is None or pattern.search(row.line):
                new_lines.append(row.line)
        _BackgroundShellManager.advance_consumed_seq(bash_id, advance_to)

        stdout = "\n".join(new_lines)
        suffix = self._status_suffix(
            status=view.status,
            exit_code=view.exit_code,
            owner_worker_id=view.owner_worker_id,
        )
        if not stdout:
            # No lines for THIS worker, but the shell may
            # still be running on the owner — surface that
            # explicitly so the LLM knows to retry rather
            # than concluding the shell is dead.
            body = (
                f"(no new output; owner worker "
                f"{view.owner_worker_id!r} may have "
                f"more — retry shortly)\n{suffix}"
            )
            return ToolResult(content=body)
        return ToolResult(content=f"{stdout}\n{suffix}")

    @staticmethod
    def _status_suffix(
        *,
        status: str,
        exit_code: int | None,
        owner_worker_id: str | None = None,
    ) -> str:
        """Format the trailing status line.

        ``exit=N`` only when the shell has a terminal exit
        code; ``running`` shells suppress it. Cross-worker
        reads prefix the worker id so the LLM can tell whose
        shell it's looking at when the fleet grows.
        """
        exit_str = f" exit={exit_code}" if exit_code is not None else ""
        if owner_worker_id:
            return f"[status] {status}{exit_str} (owner={owner_worker_id})"
        return f"[status] {status}{exit_str}"


__all__ = ["BashOutputTool"]