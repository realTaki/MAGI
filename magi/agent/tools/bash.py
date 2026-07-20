"""Shell command execution tools — bash + background-process lifecycle.

Three tools the LLM uses together:

  - :class:`BashRunTool`     — execute a command in
                                 foreground (return
                                 stdout/stderr/exit_code
                                 after the process ends)
                                 or background (return a
                                 ``bash_id`` immediately;
                                 the process keeps running
                                 and the LLM polls
                                 ``BashOutputTool``).
  - :class:`BashOutputTool`  — read new output from a
                                 background shell since
                                 the last poll. Supports
                                 optional regex filter
                                 for narrowing.
  - :class:`BashKillTool`    — terminate a background
                                 shell by id. Cleans up
                                 the monitor task + the
                                 manager registry entry.

Security posture
---------------

- The subprocess's **initial cwd is the workspace
  root** (``ToolContext.workspace``). This is the
  sanity start: ``pwd`` lands on the workspace.
  We do NOT enforce stay-inside-workspace on
  subsequent ``cd`` calls — the LLM is trusted to
  stay inside the tree, matching the reference bash
  tool's posture. The actual safety boundary is the
  operator's container / deploy boundary (the
  runtime runs as the deployer's user; the workspace
  is operator-owned). See
  :mod:`magi.agent.tools._safe_path` for the same
  trust model on the file tools.
- **OS detection at construction.** We pick bash vs
  PowerShell once at import time and never branch at
  call time. Deployers on Windows get the PowerShell
  surface; everyone else gets bash.
- **Timeouts cap at 600 s.** A misbehaving command
  can't pin an event loop forever; the foreground
  path returns ``is_error=True`` on timeout.

Why a single file with three classes
------------------------------------

Each tool is small (~50 lines) and they share a
:class:`BackgroundShellManager` singleton — splitting
into three files would mean three ``import`` lines
where the agent loop sees one. Keep them together
until a class grows past 200 lines or needs a separate
test fixture.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from magi.agent.tools.base import Tool, ToolContext, ToolResult


logger = logging.getLogger("magi.agent.tools.bash")

# Cap on a foreground command. Mirrors the reference
# implementation's ``max: 600`` — a deployer who wants
# longer can set it explicitly, but the default keeps a
# runaway ``npm install`` from pinning the event loop.
_FOREGROUND_TIMEOUT_MAX = 600
_FOREGROUND_TIMEOUT_DEFAULT = 120

# Bash id length. 8 hex chars is enough for ~4B
# concurrent shells; collision is detectable on
# ``BashKillTool`` (the "not found" branch surfaces a
# list of available ids).
_BASH_ID_LEN = 8


# ────────────────────────────────────────────────────────────────── #
# Background-process state
# ────────────────────────────────────────────────────────────────── #


@dataclass
class _BackgroundShell:
    """State for one running background shell.

    Pure data; the IO loop lives in
    :meth:`_BackgroundShellManager._monitor`.
    """

    bash_id: str
    command: str
    process: "asyncio.subprocess.Process"
    start_time: float
    output_lines: list[str] = field(default_factory=list)
    last_read_index: int = 0
    status: str = "running"  # running / completed / failed / terminated / error
    exit_code: int | None = None

    def add_output(self, line: str) -> None:
        self.output_lines.append(line)

    def get_new_output(self, filter_pattern: str | None = None) -> list[str]:
        """Return lines accumulated since the last
        poll, optionally filtered. Advances the read
        index so a follow-up call returns only
        *newer* output."""
        new_lines = self.output_lines[self.last_read_index:]
        self.last_read_index = len(self.output_lines)
        if filter_pattern:
            try:
                pattern = re.compile(filter_pattern)
                new_lines = [ln for ln in new_lines if pattern.search(ln)]
            except re.error:
                # Invalid regex → ignore the filter, return
                # everything (don't lose output to a typo).
                pass
        return new_lines

    def update_status(self, *, is_alive: bool, exit_code: int | None) -> None:
        if not is_alive:
            self.status = "completed" if exit_code == 0 else "failed"
            self.exit_code = exit_code
        else:
            self.status = "running"

    async def terminate(self) -> None:
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                # Process refused SIGTERM — SIGKILL it.
                self.process.kill()
        self.status = "terminated"
        self.exit_code = self.process.returncode


class _BackgroundShellManager:
    """Singleton registry of background shells + their
    monitor tasks.

    Process-global because the monitor task needs to
    outlive a single tool call (the LLM might call
    ``BashOutputTool`` seconds after the process
    started). The registry key is ``bash_id``.

    Cleanup: ``terminate`` cancels the monitor task
    *and* removes the registry entry so the dict
    doesn't grow without bound across a long-running
    process.
    """

    _shells: dict[str, _BackgroundShell] = {}
    _monitor_tasks: dict[str, asyncio.Task] = {}

    @classmethod
    def add(cls, shell: _BackgroundShell) -> None:
        cls._shells[shell.bash_id] = shell

    @classmethod
    def get(cls, bash_id: str) -> _BackgroundShell | None:
        return cls._shells.get(bash_id)

    @classmethod
    def list_ids(cls) -> list[str]:
        return list(cls._shells.keys())

    @classmethod
    async def start_monitor(cls, bash_id: str) -> None:
        """Spawn a coroutine that drains the
        subprocess's stdout into the shell's
        ``output_lines`` until the process ends."""
        shell = cls.get(bash_id)
        if shell is None:
            return
        process = shell.process

        async def _drain() -> None:
            try:
                while process.returncode is None:
                    if process.stdout is None:
                        break
                    try:
                        line = await asyncio.wait_for(
                            process.stdout.readline(), timeout=0.1
                        )
                    except asyncio.TimeoutError:
                        await asyncio.sleep(0.05)
                        continue
                    if not line:
                        break
                    shell.add_output(
                        line.decode("utf-8", errors="replace")
                            .rstrip("\n")
                    )
                # Reap the exit code.
                try:
                    returncode = await process.wait()
                except Exception:
                    returncode = -1
                shell.update_status(is_alive=False, exit_code=returncode)
            except Exception as e:
                if bash_id in cls._shells:
                    cls._shells[bash_id].status = "error"
                    cls._shells[bash_id].add_output(
                        f"monitor error: {e}"
                    )
            finally:
                # Always drop the monitor task handle so a
                # future ``terminate`` doesn't try to
                # cancel a finished coroutine.
                cls._monitor_tasks.pop(bash_id, None)

        cls._monitor_tasks[bash_id] = asyncio.create_task(_drain())

    @classmethod
    async def terminate(cls, bash_id: str) -> _BackgroundShell:
        shell = cls.get(bash_id)
        if shell is None:
            raise ValueError(f"Shell not found: {bash_id}")
        # Stop the monitor first so it doesn't race
        # with our own process.wait() / process.terminate().
        monitor = cls._monitor_tasks.pop(bash_id, None)
        if monitor is not None and not monitor.done():
            monitor.cancel()
        await shell.terminate()
        cls._shells.pop(bash_id, None)
        return shell


# ────────────────────────────────────────────────────────────────── #
# BashRunTool
# ────────────────────────────────────────────────────────────────── #


class BashRunTool(Tool):
    """Execute a shell command in foreground or background.

    Background mode is for long-running processes (dev
    servers, ``mongod``, ``python -m http.server``).
    The foreground path is bounded by a 600 s timeout.

    The subprocess's **initial cwd is the workspace
    root** (``ToolContext.workspace``). We don't
    enforce stay-inside-workspace on subsequent
    ``cd`` calls — the LLM is trusted to stay
    inside the tree, matching the reference bash
    tool's posture. The actual safety boundary is the
    operator's container / deploy boundary.
    """

    name = "bash"

    # Visible only to ``admin`` and ``assigned``
    # operators — same gate as the WebUI dashboard and
    # as ``ScheduleTaskTool`` / the action-item trio.
    # The chat path always passes the operator's role
    # through to ``handle_message(caller_role=...)`` so
    # non-eligible callers never see these tools in the
    # LLM's menu. ``MCPTool`` is intentionally permissive
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
        ctx: ToolContext,
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
        cwd = str(ctx.workspace) if ctx.workspace else None

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


# ────────────────────────────────────────────────────────────────── #
# BashOutputTool
# ────────────────────────────────────────────────────────────────── #


class BashOutputTool(Tool):
    """Retrieve new output from a background shell.

    Returns only lines accumulated since the last poll
    against the same ``bash_id`` — the LLM doesn't have
    to track what it has already read. An optional
    regex ``filter_str`` narrows to matching lines
    (handy for tailing a specific log line in a busy
    process).
    """

    name = "bash_output"

    # Visible only to ``admin`` and ``assigned``
    # operators — same gate as the WebUI dashboard and
    # as ``ScheduleTaskTool`` / the action-item trio.
    # The chat path always passes the operator's role
    # through to ``handle_message(caller_role=...)`` so
    # non-eligible callers never see these tools in the
    # LLM's menu. ``MCPTool`` is intentionally permissive
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

        shell = _BackgroundShellManager.get(bash_id)
        if shell is None:
            available = _BackgroundShellManager.list_ids()
            return ToolResult(
                content=(
                    f"Shell not found: {bash_id}. "
                    f"Available: {available or 'none'}."
                ),
                is_error=True,
            )

        new_lines = shell.get_new_output(filter_pattern=filter_str)
        stdout = "\n".join(new_lines)
        # Surface status so the LLM knows whether to
        # expect more output or finish polling.
        exit_str = (
            f" exit={shell.exit_code}"
            if shell.exit_code is not None
            else ""
        )
        suffix = f"[status] {shell.status}{exit_str}"

        if not stdout:
            return ToolResult(
                content=f"(no new output)\n{suffix}",
            )
        return ToolResult(
            content=f"{stdout}\n{suffix}",
        )


# ────────────────────────────────────────────────────────────────── #
# BashKillTool
# ────────────────────────────────────────────────────────────────── #


class BashKillTool(Tool):
    """Terminate a background bash shell.

    Graceful SIGTERM first (5 s grace), then SIGKILL
    if the process refuses to exit. Cleans up the
    monitor task and the registry entry so the
    background-state dict doesn't grow without bound.
    """

    name = "bash_kill"

    # Visible only to ``admin`` and ``assigned``
    # operators — same gate as the WebUI dashboard and
    # as ``ScheduleTaskTool`` / the action-item trio.
    # The chat path always passes the operator's role
    # through to ``handle_message(caller_role=...)`` so
    # non-eligible callers never see these tools in the
    # LLM's menu. ``MCPTool`` is intentionally permissive
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


__all__ = [
    "BashRunTool",
    "BashOutputTool",
    "BashKillTool",
]