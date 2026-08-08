"""Background-shell termination — :class:`BashKillTool`.

Two termination paths, picked automatically:

- **Same worker** — we own the subprocess; SIGTERM (5 s
  grace), then SIGKILL if it refuses to die. Cleans up the
  monitor task and the local registry entry.
- **Cross worker** — we don't own the subprocess; flip
  ``kill_requested=1`` on the row in
  :class:`~magi.new_bus.library.local.shellBook.BackgroundShellBook`
  and let the owner's monitor task pick it up on its next
  loop iteration. The owner's ``finalize`` then propagates
  the terminal status back to the row.

Idempotent: a second call (whether from this worker or
another) on an already-terminated shell is a successful
no-op — the LLM can retry without seeing a false
``is_error``.

If the LLM wants the trailing ``Killed.`` confirmation to
include the last lines the shell wrote, we still drain what
we can see before termination — the local buffer when
same-worker, the line Book when cross-worker.
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
        bash_id = (kwargs.get("bash_id") or "").strip()
        if not bash_id:
            return ToolResult(content="bash_id is required", is_error=True)

        # Local path — we own this subprocess. Drain the
        # tail before termination so the LLM sees what the
        # process wrote just before it died, then SIGTERM /
        # SIGKILL through the manager.
        local = _BackgroundShellManager.get(bash_id)
        if isinstance(local, _BackgroundShell):
            tail = "\n".join(local.get_new_output())
            try:
                await _BackgroundShellManager.terminate(
                    bash_id=bash_id, bus=ctx.bus,
                )
            except ValueError as e:
                # The monitor task already finished and
                # popped the entry under us — the shell is
                # effectively gone. Treat as a successful
                # no-op so the LLM retry doesn't surface a
                # false error.
                available = _BackgroundShellManager.list_ids(bus=ctx.bus)
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

        # Cross-worker path — we don't own the subprocess.
        # Drain the tail from the line Book so the LLM sees
        # what the owner wrote last, then flip
        # ``kill_requested`` so the owner's monitor ends the
        # process on its next loop iteration.
        if ctx.bus is not None:
            cross = _BackgroundShellManager.view(
                bash_id=bash_id, bus=ctx.bus,
            )
            if isinstance(cross, _CrossWorkerView):
                tail = await self._tail_from_book(
                    bash_id=bash_id, bus=ctx.bus,
                )
                requested = _BackgroundShellManager.request_kill(
                    bash_id=bash_id, bus=ctx.bus,
                )
                if not requested:
                    # Row vanished between ``view`` and
                    # ``request_kill`` — concurrent owner
                    # reap. Idempotent no-op.
                    available = _BackgroundShellManager.list_ids(
                        bus=ctx.bus,
                    )
                    return ToolResult(
                        content=(
                            f"Shell {bash_id!r} no longer "
                            f"exists. Available: "
                            f"{available or 'none'}. "
                            "(idempotent — already gone.)"
                        ),
                        is_error=True,
                    )
                body = (
                    f"Kill requested on worker "
                    f"{cross.owner_worker_id!r}; it will "
                    f"end the subprocess on its next loop "
                    f"iteration. Use ``bash_output`` to "
                    f"poll for the terminal status."
                )
                if tail:
                    body = f"Last output before kill:\n{tail}\n{body}"
                return ToolResult(content=body)

        # Not local, not in DB — same idempotent recovery as
        # the local path.
        available = _BackgroundShellManager.list_ids(bus=ctx.bus)
        return ToolResult(
            content=(
                f"Shell not found: {bash_id}. "
                f"Available: {available or 'none'}. "
                "(idempotent — already gone or never "
                "registered; nothing to kill.)"
            ),
            is_error=True,
        )

    @staticmethod
    async def _tail_from_book(*, bash_id: str, bus: Any) -> str:
        """Last few lines from the line Book — best-effort.

        Used by the cross-worker kill path so the LLM sees
        what the owner wrote just before its kill took
        effect. Failure to fetch is silently swallowed — a
        missing tail is a minor loss, but the kill itself
        must still go through.
        """
        try:
            after = _BackgroundShellManager.consumed_seq(bash_id)
            rows = bus.background_shell_lines_book.list_after(
                bash_id=bash_id, after_seq=max(after - 8, 0), limit=8,
            )
            return "\n".join(r.line for r in rows)
        except Exception:
            logger.exception(
                "bash_kill: tail fetch failed (bash_id=%s)",
                bash_id,
            )
            return ""


__all__ = ["BashKillTool"]