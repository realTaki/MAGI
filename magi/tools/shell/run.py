"""Foreground / background command execution — :class:`BashRunTool`.

The subprocess's **initial cwd is the workspace root**
(``ToolContext.workspace``). We don't enforce stay-
inside-workspace on subsequent ``cd`` calls — the LLM
is trusted to stay inside the tree, matching the
reference bash tool's posture. The actual safety
boundary is the operator's container / deploy boundary
(see :mod:`magi.tools._safe_path` for the same trust
model on the file tools).

Timeouts cap at 600 s — a misbehaving command can't pin
an event loop forever; the foreground path returns
``is_error=True`` on timeout. Background processes have
no timeout; they live until :class:`BashKillTool` ends
them.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import time
import uuid
from typing import Any

from magi.tools.base import Tool, ToolContext, ToolResult
from magi.tools.shell._manager import (
    _BASH_ID_LEN,
    _BackgroundShell,
    _BackgroundShellManager,
    _FOREGROUND_TIMEOUT_DEFAULT,
    _FOREGROUND_TIMEOUT_MAX,
)


logger = logging.getLogger("magi.tools.shell.run")


class BashRunTool(Tool):
    """Execute a shell command in foreground or background.

    Background mode is for long-running processes (dev
    servers, ``mongod``, ``python -m http.server``).
    The foreground path is bounded by a 600 s timeout.
    """

    name = "bash"

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

    def _build_description(self) -> str:
        """OS-specific description block.

        Different shells (bash vs PowerShell) have
        different idioms for chaining commands
        (``&&`` vs ``;``), path quoting, and the
        long-running-process pattern. We render the
        relevant version up-front so the LLM picks
        the right syntax on the first call — it's
        easier than reading generic text and
        re-deriving the platform rules.

        Body unchanged across platforms; only the
        example block (chain syntax, sample long-
        running command) flips.
        """
        if self.is_windows:
            examples_block = (
                "Tips:\n"
                "  - Quote file paths with spaces: cd \"My Documents\"\n"
                "  - Chain dependent commands with semicolon: "
                "git add . ; git commit -m \"msg\"\n"
                "  - Use absolute paths instead of cd when possible\n"
                "  - For background commands, monitor with bash_output "
                "and terminate with bash_kill\n\n"
                "Examples:\n"
                "  - git status\n"
                "  - npm test\n"
                "  - python -m http.server 8080 "
                "(with run_in_background=true)"
            )
        else:
            examples_block = (
                "Tips:\n"
                "  - Quote file paths with spaces: cd \"My Documents\"\n"
                "  - Chain dependent commands with &&: "
                "git add . && git commit -m \"msg\"\n"
                "  - Use absolute paths instead of cd when possible\n"
                "  - For background commands, monitor with bash_output "
                "and terminate with bash_kill\n\n"
                "Examples:\n"
                "  - git status\n"
                "  - npm test\n"
                "  - python3 -m http.server 8080 "
                "(with run_in_background=true)"
            )
        return (
            f"Execute a {self.shell_name} command in foreground (return "
            "stdout/stderr/exit_code) or background (return a bash_id; "
            "poll with bash_output, kill with bash_kill).\n\n"
            "For terminal operations like git, npm, docker, curl, etc. "
            "DO NOT use for file operations — use the read_file / "
            "write_file / list_files tools. Those validate paths "
            "against the workspace and avoid the cost of forking a "
            "process.\n\n"
            "Parameters:\n"
            "  - command (required): the command to execute. "
            "Quote file paths with spaces using double quotes.\n"
            "  - timeout (optional): timeout in seconds for foreground "
            f"commands (default 120, max 600). Ignored when run_in_background=true.\n"
            "  - run_in_background (optional): set true for long-running "
            "commands (servers, mongod, etc.). Returns a bash_id "
            "immediately; poll with bash_output, terminate with "
            "bash_kill.\n\n"
            + examples_block
        )

    @property
    def description(self) -> str:
        # Built lazily on first read. The reference
        # implementation builds it in __init__; we
        # delay because the LLM schema is rendered
        # at first registry access, not at every
        # BashRunTool construction.
        cached = getattr(self, "_description_cache", None)
        if cached is not None:
            return cached
        cached = self._build_description()
        self._description_cache = cached
        return cached

    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": (
                    "The shell command to execute. Quote file "
                    "paths with spaces using double quotes. "
                    "Chain with && (Unix) or ; (Windows)."
                ),
            },
            "timeout": {
                "type": "integer",
                "description": (
                    "Foreground-only timeout in seconds. "
                    f"Default {_FOREGROUND_TIMEOUT_DEFAULT}, "
                    f"max {_FOREGROUND_TIMEOUT_MAX}. Ignored "
                    "when ``run_in_background=true``."
                ),
                "default": _FOREGROUND_TIMEOUT_DEFAULT,
            },
            "run_in_background": {
                "type": "boolean",
                "description": (
                    "Set true for long-running commands. The "
                    "tool returns immediately with a ``bash_id``; "
                    "poll output with bash_output, terminate "
                    "with bash_kill."
                ),
                "default": False,
            },
        },
        "required": ["command"],
    }

    def __init__(self) -> None:
        # OS detection once at construction. The reference
        # code does this per-instance; we do it per-class
        # because the class is registered as a singleton
        # in the tool registry.
        self.is_windows = platform.system() == "Windows"
        self.shell_name = "PowerShell" if self.is_windows else "bash"
        # Lazily populated by ``description``; cleared on
        # each instance so the property is correct if
        # ``is_windows`` is ever mutated (v0: never).
        self._description_cache: str | None = None

    # -- run -----------------------------------------------------------

    async def run(
        self,
        _ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        command = (kwargs.get("command") or "").strip()
        if not command:
            return ToolResult(
                content="command is required",
                is_error=True,
            )

        # Clamp the foreground timeout. The reference
        # code clamps inside the execute method; we do it
        # at the boundary so the schema's default is the
        # single source of truth.
        try:
            timeout = int(kwargs.get("timeout") or _FOREGROUND_TIMEOUT_DEFAULT)
        except (TypeError, ValueError):
            timeout = _FOREGROUND_TIMEOUT_DEFAULT
        if timeout < 1:
            timeout = _FOREGROUND_TIMEOUT_DEFAULT
        if timeout > _FOREGROUND_TIMEOUT_MAX:
            timeout = _FOREGROUND_TIMEOUT_MAX

        run_in_background = bool(kwargs.get("run_in_background"))
        cwd = str(_ctx.workspace) if _ctx.workspace else None

        try:
            if run_in_background:
                return await self._run_background(command, cwd)
            return await self._run_foreground(command, timeout, cwd)
        except Exception as e:
            logger.exception("bash tool: unexpected error")
            return ToolResult(
                content=f"bash tool error: {e}",
                is_error=True,
            )

    # -- helpers -------------------------------------------------------

    async def _run_background(
        self,
        command: str,
        cwd: str | None,
    ) -> ToolResult:
        bash_id = uuid.uuid4().hex[:_BASH_ID_LEN]
        if self.is_windows:
            # PowerShell: -NoProfile avoids loading
            # the user's $PROFILE which can hang on
            # network drives.
            process = await asyncio.create_subprocess_exec(
                "powershell.exe", "-NoProfile", "-Command", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,  # combine
                cwd=cwd,
            )
        else:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,  # combine
                cwd=cwd,
            )

        shell = _BackgroundShell(
            bash_id=bash_id,
            command=command,
            process=process,
            start_time=time.time(),
        )
        _BackgroundShellManager.add(shell)
        await _BackgroundShellManager.start_monitor(bash_id)

        return ToolResult(
            content=(
                f"Command started in background. "
                f"Use bash_output to monitor "
                f"(bash_id='{bash_id}').\n\n"
                f"Command: {command}\n"
                f"Bash ID: {bash_id}"
            ),
        )

    async def _run_foreground(
        self,
        command: str,
        timeout: int,
        cwd: str | None,
    ) -> ToolResult:
        if self.is_windows:
            process = await asyncio.create_subprocess_exec(
                "powershell.exe", "-NoProfile", "-Command", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
        else:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            # Best-effort reap so the OS doesn't leak
            # zombies.
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass
            return ToolResult(
                content=(
                    f"Command timed out after {timeout}s and was killed.\n"
                    f"Re-run with a higher ``timeout`` "
                    f"(max {_FOREGROUND_TIMEOUT_MAX}s) or use "
                    f"``run_in_background=true``."
                ),
                is_error=True,
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        returncode = process.returncode

        # Format: stdout + (stderr if any) + exit code line
        # so the LLM has the failure mode in one read.
        parts: list[str] = []
        if stdout:
            parts.append(stdout.rstrip("\n"))
        if stderr:
            parts.append(f"[stderr]\n{stderr.rstrip('\n')}")
        parts.append(f"[exit_code] {returncode}")
        if not stdout and not stderr:
            parts.append("(no output)")

        return ToolResult(
            content="\n".join(parts),
            is_error=returncode != 0,
        )


__all__ = ["BashRunTool"]