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
import logging
import re
import time
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum

logger = logging.getLogger("magi.tools.shell._manager")

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

# Per-shell stdout cap. A background ``npm run dev`` left
# alone for hours would otherwise pin every line it ever
# wrote in memory — the LLM only ever reads forward, so
# holding the full history buys nothing. When the cap is
# hit the oldest lines are dropped and counted; the count
# is surfaced to the LLM so "my output has a hole" is
# visible rather than silent.
_MAX_BUFFERED_LINES = 5000

# Retention for shells that reached a terminal state. They
# can't be evicted on completion — the LLM polls
# ``bash_output`` *after* a command finishes, and that's the
# whole point of the background mode. So they're kept for a
# grace window, then reaped. Both bounds apply: whichever
# trips first wins.
_COMPLETED_TTL_SECONDS = 300
_MAX_COMPLETED_RETAINED = 32


class ShellStatus(StrEnum):
    """Background-shell lifecycle state stored on ``_BackgroundShell.status``.

    In-memory only — the shell registry is process-local and
    nothing persists this column, so the enum is a typo-guard
    rather than a DB constraint. ``StrEnum`` keeps
    ``status == "completed"`` style comparisons in tests /
    formatting (``f"[status] {shell.status}"`` in
    :mod:`magi.tools.shell.output`) working unchanged because
    every member is still a ``str``. Mirrors
    :class:`magi.bus.firmwares.books.local.contactBook.NoteKind`.
    """

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TERMINATED = "terminated"
    ERROR = "error"


# Terminal statuses — a shell in one of these has no live
# subprocess behind it and is eligible for reaping.
_TERMINAL_STATUSES = frozenset(
    {ShellStatus.COMPLETED, ShellStatus.FAILED, ShellStatus.TERMINATED, ShellStatus.ERROR}
)


@dataclass
class _BackgroundShell:
    """State for one running background shell.

    Pure data; the IO loop lives in
    :meth:`_BackgroundShellManager._monitor`.
    """

    bash_id: str
    command: str
    process: asyncio.subprocess.Process
    start_time: float
    output_lines: list[str] = field(default_factory=list)
    last_read_index: int = 0
    status: ShellStatus = ShellStatus.RUNNING  # running / completed / failed / terminated / error
    exit_code: int | None = None
    #: When the shell reached a terminal status — drives the
    #: reaper's TTL. ``None`` while still running.
    ended_at: float | None = None
    #: How many lines fell off the front of the buffer because
    #: of :data:`_MAX_BUFFERED_LINES`. Surfaced to the LLM so a
    #: gap in the output is explicit.
    dropped_lines: int = 0

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    def add_output(self, line: str) -> None:
        self.output_lines.append(line)
        # Trim from the front once the cap is exceeded. We append
        # one line at a time so ``overflow`` is normally 1, but the
        # arithmetic is written generally so a future batched
        # append doesn't silently corrupt the read cursor.
        overflow = len(self.output_lines) - _MAX_BUFFERED_LINES
        if overflow > 0:
            del self.output_lines[:overflow]
            self.dropped_lines += overflow
            # Shift the cursor by the same amount so "new since
            # last poll" still means the same thing. Clamping at 0
            # is the case where the dropped lines were never read —
            # ``dropped_lines`` is what tells the LLM about those.
            self.last_read_index = max(0, self.last_read_index - overflow)

    def get_new_output(self, filter_pattern: str | None = None) -> list[str]:
        """Return lines accumulated since the last
        poll, optionally filtered. Advances the read
        index so a follow-up call returns only
        *newer* output."""
        new_lines = self.output_lines[self.last_read_index :]
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
            self.status = ShellStatus.COMPLETED if exit_code == 0 else ShellStatus.FAILED
            self.exit_code = exit_code
            self.ended_at = time.monotonic()
        else:
            self.status = ShellStatus.RUNNING

    def mark_error(self, message: str) -> None:
        """Terminal 'the monitor itself broke' transition."""
        self.status = ShellStatus.ERROR
        self.ended_at = time.monotonic()
        self.add_output(message)

    async def terminate(self) -> None:
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except TimeoutError:
                # Process refused SIGTERM — SIGKILL it.
                self.process.kill()
                # Reap so the OS doesn't keep a zombie around.
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self.process.wait(), timeout=2)
        self.status = ShellStatus.TERMINATED
        self.exit_code = self.process.returncode
        self.ended_at = time.monotonic()
        self._close_transport()

    def _close_transport(self) -> None:
        """Release the subprocess transport eagerly.

        ``process.wait()`` reaps the child but leaves the stdout pipe
        transport open — the monitor read from ``process.stdout``, so
        a protocol still holds it. It would normally be released when
        the ``Process`` is garbage-collected, but on the shutdown path
        that happens *after* the event loop is closed, and
        ``BaseSubprocessTransport.__del__`` then calls ``call_soon``
        on a dead loop. That surfaces as a stack trace on every MAGI
        exit that had background shells.

        ``_transport`` is private on :class:`asyncio.subprocess.Process`
        with no public equivalent; guarded so a CPython change can only
        cost us the tidy-up, never the shutdown.
        """
        transport = getattr(self.process, "_transport", None)
        if transport is None:
            return
        with suppress(Exception):
            transport.close()


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
        process: asyncio.subprocess.Process,
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
        # Reap on the only growth path. A background sweep would
        # need a timer task that outlives every tool call and has
        # to be torn down on shutdown; reaping here costs one
        # cheap pass over a dict that is bounded by construction.
        cls._reap_terminal()
        return shell

    @classmethod
    def _reap_terminal(cls) -> None:
        """Drop terminal shells past the retention window.

        Two bounds, whichever trips first:

        * age — a shell that ended more than
          :data:`_COMPLETED_TTL_SECONDS` ago is gone. The LLM
          gets a grace window to poll a finished command, not
          forever.
        * count — at most :data:`_MAX_COMPLETED_RETAINED` terminal
          shells survive, oldest evicted first. This is the
          backstop for a burst that all finishes inside the TTL.

        Running shells are never touched: they own a live
        subprocess and a monitor task.
        """
        terminal = [(s.ended_at or 0.0, bid) for bid, s in cls._shells.items() if s.is_terminal]
        if not terminal:
            return
        now = time.monotonic()
        doomed = {bid for ended, bid in terminal if now - ended > _COMPLETED_TTL_SECONDS}
        survivors = sorted(
            (t for t in terminal if t[1] not in doomed),
            key=lambda t: t[0],
        )
        excess = len(survivors) - _MAX_COMPLETED_RETAINED
        if excess > 0:
            doomed.update(bid for _, bid in survivors[:excess])
        for bid in doomed:
            cls._shells.pop(bid, None)
        if doomed:
            logger.debug(
                "background-shell registry: reaped %d terminal shell(s)",
                len(doomed),
            )

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
                        line = await asyncio.wait_for(process.stdout.readline(), timeout=0.1)
                    except TimeoutError:
                        await asyncio.sleep(0.05)
                        continue
                    if not line:
                        break
                    shell.add_output(line.decode("utf-8", errors="replace").rstrip("\n"))
                # Reap the exit code.
                try:
                    returncode = await process.wait()
                except Exception:
                    returncode = -1
                shell.update_status(is_alive=False, exit_code=returncode)
                # Same reasoning as the kill path: release the
                # transport now rather than leaving it for a GC that
                # may land after the loop is gone. A naturally
                # completed shell lingers in the registry until the
                # reaper takes it, so this is the only point where we
                # know its pipes are finished with.
                shell._close_transport()
            except Exception as e:
                if bash_id in cls._shells:
                    cls._shells[bash_id].mark_error(f"monitor error: {e}")
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
        # Stop the monitor first so it doesn't race with our own
        # process.wait() / process.terminate(). ``cancel()`` only
        # *requests* cancellation — the coroutine keeps running until
        # its next await point, so we must await it to actually be
        # sure it's done. Skipping the await leaves a pending task
        # holding the subprocess transport; it then gets collected
        # after the loop closes, which is where the
        # ``Event loop is closed`` noise on shutdown comes from.
        monitor = cls._monitor_tasks.pop(bash_id, None)
        if monitor is not None and not monitor.done():
            monitor.cancel()
            with suppress(asyncio.CancelledError):
                await monitor
        await shell.terminate()
        cls._shells.pop(bash_id, None)
        return shell

    @classmethod
    async def shutdown(cls) -> int:
        """Tear down every live background shell. Returns the count.

        Called from :meth:`magi.tools.worker.ToolsWorker.stop` so a
        MAGI shutdown doesn't strand the subprocesses it spawned.
        Without this the ``bash(run_in_background=True)`` children
        outlive the process that owns them — nothing else on the box
        knows their pids, so they leak until the container dies — and
        their monitor tasks get garbage-collected mid-``await``,
        which is what produces the ``Task was destroyed but it is
        pending`` / ``Event loop is closed`` noise on exit.

        Best-effort per shell: one subprocess refusing to die must
        not block the rest of the teardown.
        """
        bash_ids = list(cls._shells)
        killed = 0
        for bash_id in bash_ids:
            try:
                await cls.terminate(bash_id)
                killed += 1
            except ValueError:
                # Raced with the monitor's own reap — already gone.
                continue
            except Exception:
                logger.exception(
                    "background-shell shutdown: failed to terminate %s",
                    bash_id,
                )
        # Anything left is a shell whose terminate() raised; drop the
        # bookkeeping regardless so a restarted worker starts clean.
        for task in cls._monitor_tasks.values():
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        cls._monitor_tasks.clear()
        cls._shells.clear()
        if killed:
            logger.info(
                "background-shell shutdown: terminated %d shell(s)",
                killed,
            )
        return killed


# -- public seam ---------------------------------------------------------


async def shutdown_background_shells() -> int:
    """Terminate every live background shell. Returns the count.

    The one name in this module intended for callers outside
    :mod:`magi.tools.shell` — :class:`~magi.tools.worker.ToolsWorker`
    calls it on stop. Follows the same convention as
    :func:`magi.tools._safe_path.safe_resolve`: a private module
    exporting a public function, rather than the package ``__init__``
    growing code (every sibling ``magi/tools/*/__init__.py`` is
    docs-only).

    Idempotent — a second call is a no-op returning ``0``.
    """
    return await _BackgroundShellManager.shutdown()


__all__ = ["shutdown_background_shells"]
