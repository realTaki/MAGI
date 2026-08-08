"""Background-shell state + registry.

Shared by :mod:`magi.tools.shell.run`,
:mod:`magi.tools.shell.output`, and
:mod:`magi.tools.shell.kill` — the three tools that
together cover the bash subprocess lifecycle.

Why a singleton
---------------

Process-global because the monitor task needs to
outlive a single tool call (the LLM might call
``BashOutputTool`` seconds after the process started).
The registry key is ``bash_id``. ``terminate`` cancels
the monitor task *and* removes the registry entry so
the dict doesn't grow without bound across a long-
running process.

Why a private module
--------------------

This is internal to :mod:`magi.tools.shell`. External
code reaches the bash tools via the public subclasses
(:class:`BashRunTool`, etc.); the dataclass + manager
here are implementation details that the LLM never
sees.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

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
    monitor tasks."""

    _shells: dict[str, _BackgroundShell] = {}
    _monitor_tasks: dict[str, asyncio.Task] = {}

    @classmethod
    def add(
        cls,
        *,
        bash_id: str,
        command: str,
        process: "asyncio.subprocess.Process",
        start_time: float,
    ) -> _BackgroundShell:
        """Register a new background shell and return it.

        The manager owns construction so callers can't hand us a
        half-built dataclass or register one under a key that
        disagrees with ``shell.bash_id``.
        """
        shell = _BackgroundShell(
            bash_id=bash_id,
            command=command,
            process=process,
            start_time=start_time,
        )
        cls._shells[bash_id] = shell
        return shell

    @classmethod
    def get(cls, bash_id: str) -> _BackgroundShell | None:
        return cls._shells.get(bash_id)

    @classmethod
    def list_ids(cls) -> list[str]:
        return list(cls._shells.keys())

    @classmethod
    async def start_monitor(cls, *, bash_id: str) -> None:
        """Spawn a coroutine that drains the
        subprocess's stdout into the shell's
        ``output_lines`` until the process ends."""
        shell = cls.get(bash_id)
        if shell is None:
            return
        process = shell.process

        async def _drain() -> None:
            try:
                # Drain until the pipe closes (EOF on
                # ``readline``), NOT until ``returncode`` is
                # set. The two are not atomic — the kernel can
                # close stdout the instant the process exits
                # while Python's subprocess machinery takes a
                # tick or two to set ``returncode``. Gating the
                # loop on ``returncode is None`` breaks out
                # early on EOF and loses whatever the kernel
                # still had buffered in the pipe (short bursts
                # like ``echo a; echo b; echo c`` trip this
                # reliably). The ``readline`` timeout handles
                # "alive but idle"; ``break`` on EOF handles
                # "process exited".
                while True:
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