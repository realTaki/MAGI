"""Background-shell state + cross-worker registry.

Shared by :mod:`magi.tools.shell.run`,
:mod:`magi.tools.shell.output`, and
:mod:`magi.tools.shell.kill` — the three tools that together
cover the bash subprocess lifecycle.

What lives where
----------------

The ``asyncio.subprocess.Process`` **must** stay in the process
that spawned it (``readline()`` and ``terminate()`` are bound to
the spawning event loop). So the live process handle +
monitor task + in-memory line buffer live in process-local
class attributes on :class:`_BackgroundShellManager`.

Cross-worker visibility goes through new_bus:

- :class:`~magi.new_bus.library.local.shellBook.BackgroundShellBook`
  holds one row per shell (``bash_id``, ``owner_worker_id``,
  ``status``, ``exit_code``, ``kill_requested``, ``last_seq``).
- :class:`~magi.new_bus.library.local.shellBook.BackgroundShellLineBook`
  appends every drained stdout line so a non-owning worker can
  still ``bash_output`` against the shell.

Why the dual write
------------------

The owner worker holds an in-memory ring buffer for the fast
same-worker ``bash_output`` path — no DB round-trip when the
LLM is talking to the worker that spawned the shell. Every line
the owner drains is **also** appended to the line Book; that
mirror is what non-owner workers read on cross-worker polls.

A ``bash_kill`` from a non-owner worker sets
``kill_requested=1`` on the row; the owner's monitor task
checks the flag each loop iteration (cheap — single indexed
UPDATE) and terminates the subprocess when set.

Why a singleton (class-level dict)
----------------------------------

Same reason as the original design: the monitor task needs to
outlive a single tool call (the LLM might call
``BashOutputTool`` seconds after the process started). With
multi-worker support added, "outlive" now means "outlive the
tool call AND be visible from sibling worker processes", which
is exactly what the new_bus Books provide.

Why a private module
--------------------

Internal to :mod:`magi.tools.shell`. External code reaches the
bash tools via the public subclasses (:class:`BashRunTool`,
:class:`BashOutputTool`, :class:`BashKillTool`); the dataclass
+ manager here are implementation details the LLM never sees.

Single-worker mode (``bus=None``)
---------------------------------

Tests and boot probes hand the tool a ``ToolContext`` with
``ctx.bus is None``. The manager tolerates this by short-circuiting
all cross-worker paths: ``add()`` skips the DB write, ``get()``
falls back to the local dict, ``request_kill()`` is a no-op.
The fast same-worker path is unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from magi.new_bus import NewBus

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

# How many lines the owner monitor batches into one DB
# ``bump_seq`` call. Smaller = tighter cross-worker visibility
# at the cost of more writes; larger = batchier writes with
# more cross-worker lag. 16 is the sweet spot — a chatty
# process (``pytest -v``) still sees near-real-time output
# from a sibling worker, and the DB write rate stays under
# ~10/sec even at 100 lines/sec.
_BUMP_SEQ_BATCH = 16


# -- per-process state ----------------------------------------------------


@dataclass
class _BackgroundShell:
    """State for one running background shell owned by THIS process.

    Pure data; the IO loop lives in
    :meth:`_BackgroundShellManager._monitor`.

    Cross-worker fields (``status`` / ``exit_code`` /
    ``kill_requested``) are mirrored in the DB row — when the
    owner flips them locally, it also flips them via
    :class:`BackgroundShellBook` so non-owner workers see the
    change. ``last_flushed_seq`` tracks how many lines have
    already been pushed to the line Book so the monitor can
    batch-up ``bump_seq`` calls.
    """

    bash_id: str
    command: str
    process: "asyncio.subprocess.Process"
    start_time: float
    owner_worker_id: str
    output_lines: list[str] = field(default_factory=list)
    last_read_index: int = 0
    status: str = "running"  # running / completed / failed / terminated / error
    exit_code: int | None = None
    next_seq: int = 1  # next per-shell monotonic seq for the line Book
    last_flushed_seq: int = 0  # last seq already written to the line Book

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


# -- cross-worker registry adapter ---------------------------------------


def _books(bus: "NewBus | None") -> tuple[Any, Any]:
    """Return ``(shells_book, lines_book)`` or ``(None, None)`` if
    no bus is wired.

    Centralised so every Book call site has one check instead of
    repeating ``if bus is None: return …``. The Books are
    constructed by :func:`magi.new_bus.bootstrap.bootstrap_new_bus`;
    if the bootstrap path didn't run (a unit test mocking the bus
    out, say), the manager degrades to local-only mode.
    """
    if bus is None:
        return None, None
    return bus.background_shells_book, bus.background_shell_lines_book


@dataclass
class _CrossWorkerView:
    """A read-only view of a shell living in another worker.

    Returned by :meth:`_BackgroundShellManager.get` when the
    bash_id is in the DB row but not in this worker's local
    dict. Carries enough state for ``BashOutputTool`` /
    ``BashKillTool`` to act without ever touching the live
    subprocess (which lives in another process and can't be
    reached from here anyway).

    ``last_seq`` is the high-water mark the owner has flushed;
    readers track their own consumed-seq on top of this.
    """

    bash_id: str
    owner_worker_id: str
    command: str
    status: str
    exit_code: int | None
    last_seq: int
    kill_requested: bool


class _BackgroundShellManager:
    """Per-process registry of background shells + their
    monitor tasks, with cross-worker visibility via new_bus.

    The class-level dicts hold **process-local** state only —
    the live ``asyncio.subprocess.Process`` handle, the
    monitor task, and the fast-path in-memory line buffer. The
    DB row mirrors ``bash_id`` / ``owner_worker_id`` /
    ``status`` / ``exit_code`` / ``kill_requested`` /
    ``last_seq`` and is the source of truth for cross-worker
    readers.

    ``_consumed_seq`` tracks per-bash-id consumed-seq for
    cross-worker ``bash_output`` polls inside THIS worker, so a
    follow-up call returns only newer lines without re-reading
    what the LLM has already seen.
    """

    _shells: dict[str, _BackgroundShell] = {}
    _monitor_tasks: dict[str, asyncio.Task] = {}
    _consumed_seq: dict[str, int] = {}

    # -- registration ----------------------------------------------------

    @classmethod
    def add(
        cls,
        *,
        bash_id: str,
        command: str,
        process: "asyncio.subprocess.Process",
        owner_worker_id: str,
        start_time: float,
        bus: "NewBus | None",
        uid: int,
    ) -> _BackgroundShell:
        """Register a new background shell.

        Always populates the local dict (the monitor task +
        in-memory buffer must live here). Persists a row in
        :class:`BackgroundShellBook` when ``bus`` is wired —
        the row is the cross-worker rendezvous point.
        """
        shell = _BackgroundShell(
            bash_id=bash_id,
            command=command,
            process=process,
            start_time=start_time,
            owner_worker_id=owner_worker_id,
        )
        cls._shells[bash_id] = shell
        shells_book, _ = _books(bus)
        if shells_book is not None:
            try:
                shells_book.add(
                    bash_id=bash_id,
                    command=command,
                    owner_worker_id=owner_worker_id,
                    uid=uid,
                )
            except Exception:
                # The local dict is the source of truth for
                # THIS process; a DB write failure mustn't
                # break the tool call. Log and keep going —
                # cross-worker reads will see a "not found"
                # until the next process restart reaps.
                logger.exception(
                    "background-shell registry: row insert "
                    "failed for bash_id=%s",
                    bash_id,
                )
        return shell

    @classmethod
    def get(cls, bash_id: str) -> _BackgroundShell | _CrossWorkerView | None:
        """Look up a shell — local first, then DB.

        Returns:
          * ``_BackgroundShell`` — process-local handle (fast
            same-worker path).
          * ``_CrossWorkerView`` — DB-only metadata (read-only
            view used by cross-worker ``bash_output`` /
            ``bash_kill``).
          * ``None`` — neither local nor in the DB.
        """
        local = cls._shells.get(bash_id)
        if local is not None:
            return local
        return None  # cross-worker lookup needs a bus; see ``view``

    @classmethod
    def view(cls, *, bash_id: str, bus: "NewBus | None") -> _CrossWorkerView | None:
        """DB-backed lookup for cross-worker readers.

        Separate from :meth:`get` because :meth:`get` is on the
        hot path of same-worker code (which never needs the
        bus) and threading a bus param through every call
        would dirty that signature. Cross-worker callers ask
        for the view explicitly.
        """
        shells_book, _ = _books(bus)
        if shells_book is None:
            return None
        row = shells_book.get(bash_id=bash_id)
        if row is None:
            return None
        return _CrossWorkerView(
            bash_id=row.bash_id,
            owner_worker_id=row.owner_worker_id,
            command=row.command,
            status=row.status,
            exit_code=row.exit_code,
            last_seq=row.last_seq,
            kill_requested=bool(row.kill_requested),
        )

    @classmethod
    def list_ids(cls, *, bus: "NewBus | None" = None) -> list[str]:
        """Bash ids visible from this worker — local + DB.

        The list mixes local ids and ids owned by other
        workers, so :class:`BashKillTool`'s "not found"
        recovery can show the operator what's actually
        running across the fleet. Local ids come first so the
        typical same-worker case still sees them in insertion
        order.
        """
        local = list(cls._shells.keys())
        shells_book, _ = _books(bus)
        if shells_book is None:
            return local
        try:
            # Cheap-ish: the DB row list grows over time but
            # only completed / orphaned shells accumulate
            # forever. v0 doesn't age them out; the operator
            # can prune via a future admin tool. For the
            # "not found" recovery path this is fine.
            from magi.new_bus.library.local.shellBook import (
                BackgroundShellBook as _BSB,
            )
            # The Book exposes one row at a time via ``get``;
            # a fleet-wide enumeration isn't a Book method in
            # v0. Cheapest correct impl: scan a small SQL via
            # the session. Done inline so we don't grow the
            # Book's API for a single use.
            with shells_book._session() as s:
                from sqlalchemy import select
                from magi.new_bus.library.local.shellBook import (
                    _BackgroundShellRow,
                )
                rows = s.scalars(
                    select(_BackgroundShellRow.bash_id)
                ).all()
                db_ids = [r for r in rows]
        except Exception:
            logger.exception(
                "background-shell registry: cross-worker "
                "id list failed; returning local only"
            )
            return local
        # De-dup + local-first ordering.
        seen = set(local)
        merged = list(local)
        for bid in db_ids:
            if bid not in seen:
                merged.append(bid)
                seen.add(bid)
        return merged

    # -- cross-worker consume tracker -----------------------------------

    @classmethod
    def consumed_seq(cls, bash_id: str) -> int:
        """Last seq THIS worker consumed from a cross-worker shell.

        Used by ``BashOutputTool`` on cross-worker reads so a
        follow-up call returns only newer lines. Local shells
        don't go through this — they have their own
        ``last_read_index`` on the dataclass.
        """
        return cls._consumed_seq.get(bash_id, 0)

    @classmethod
    def advance_consumed_seq(cls, bash_id: str, seq: int) -> None:
        """Bump the cross-worker consumed-seq to ``seq``.

        Idempotent: a smaller ``seq`` (e.g. an empty poll)
        doesn't regress the cursor.
        """
        cur = cls._consumed_seq.get(bash_id, 0)
        if seq > cur:
            cls._consumed_seq[bash_id] = seq

    # -- monitor task ----------------------------------------------------

    @classmethod
    async def start_monitor(
        cls,
        *,
        bash_id: str,
        bus: "NewBus | None",
    ) -> None:
        """Spawn a coroutine that drains the subprocess's stdout.

        Three responsibilities in one loop:

        1. Drain ``process.stdout.readline()`` and append to the
           local buffer (same-worker fast path).
        2. Mirror each line into the line Book so non-owner
           workers can ``bash_output`` against this shell.
        3. Check ``kill_requested`` once per loop iteration
           and terminate the subprocess if a sibling worker
           called ``BashKillTool``.

        The cross-worker DB calls are best-effort: a SQLite
        hiccup must not kill the monitor task. The worst case
        is "non-owner can't see this line for one batch"; the
        shell's terminal status still propagates because
        ``finalize`` is called from this same loop on the
        normal exit path.
        """
        shell = cls._shells.get(bash_id)
        if shell is None:
            return
        process = shell.process
        shells_book, lines_book = _books(bus)

        async def _drain() -> None:
            try:
                # Drain until the pipe closes (EOF on
                # ``readline``), NOT until ``returncode``
                # is set. The two are not atomic — the
                # kernel can close stdout the instant the
                # process exits while Python's subprocess
                # machinery takes a tick or two to set
                # ``returncode``. Gating the loop on
                # ``returncode is None`` would break out
                # early on EOF and lose any lines the
                # kernel still had in the pipe buffer
                # (small bursts like ``echo a; echo b;
                # echo c`` trip this race reliably).
                # The timeout on ``readline`` handles the
                # "process alive but idle" case; ``break``
                # on EOF handles the "process exited" case.
                while True:
                    if process.stdout is None:
                        break
                    # Cross-worker kill check — atomic
                    # read-and-clear via an indexed UPDATE.
                    # Cheap: single statement on an indexed
                    # PK. Skipped when there's no bus (no
                    # one to receive the kill signal).
                    if shells_book is not None:
                        try:
                            import sys
                            print(f"[DRAIN-LOOP] {bash_id} checking kill_requested", file=sys.stderr)
                            if shells_book.claim_kill_request(
                                bash_id=bash_id,
                            ):
                                print(f"[DRAIN-LOOP] {bash_id} KILL DETECTED, terminating process", file=sys.stderr)
                                # Owner flips local status
                                # + exit_code so same-worker
                                # bash_output sees the
                                # transition immediately AND
                                # so the post-loop
                                # ``update_status`` doesn't
                                # overwrite ``status``
                                # back to "completed" /
                                # "failed" (it would — its
                                # contract is to derive the
                                # terminal label from
                                # exit_code).
                                shell.status = "terminated"
                                process.terminate()
                                print(f"[DRAIN-LOOP] {bash_id} terminate() returned, breaking", file=sys.stderr)
                                break
                        except Exception:
                            logger.exception(
                                "background-shell monitor: "
                                "kill-claim check failed "
                                "(bash_id=%s)",
                                bash_id,
                            )
                    import sys
                    print(f"[DRAIN] {bash_id} post-kill-check, about to readline", file=sys.stderr)
                    try:
                        line = await asyncio.wait_for(
                            process.stdout.readline(), timeout=0.1
                        )
                        print(f"[DRAIN] {bash_id} readline returned {line!r}", file=sys.stderr)
                    except asyncio.TimeoutError:
                        print(f"[DRAIN] {bash_id} readline timeout", file=sys.stderr)
                        await asyncio.sleep(0.05)
                        continue
                    if not line:
                        # EOF — pipe closed (process exited
                        # or our stdin was severed). Reap
                        # below; any data still in the
                        # kernel buffer was already drained
                        # by the previous readline() calls.
                        print(f"[DRAIN] {bash_id} EOF, breaking", file=sys.stderr)
                        break
                    decoded = (
                        line.decode("utf-8", errors="replace")
                        .rstrip("\n")
                    )
                    shell.add_output(decoded)
                    # Mirror to the line Book — single row
                    # append. The ``next_seq`` is the
                    # per-shell monotonic counter.
                    if lines_book is not None:
                        try:
                            lines_book.append(
                                bash_id=bash_id,
                                seq=shell.next_seq,
                                line=decoded,
                            )
                            shell.next_seq += 1
                            # Batched bump_seq: avoid a DB
                            # write per line. _BUMP_SEQ_BATCH
                            # is the cross-worker lag /
                            # write-rate trade-off.
                            if (
                                shell.next_seq
                                - shell.last_flushed_seq
                                >= _BUMP_SEQ_BATCH
                            ):
                                shells_book.bump_seq(
                                    bash_id=bash_id,
                                    new_last_seq=shell.next_seq - 1,
                                )
                                shell.last_flushed_seq = (
                                    shell.next_seq - 1
                                )
                        except Exception:
                            logger.exception(
                                "background-shell monitor: "
                                "line mirror failed "
                                "(bash_id=%s)",
                                bash_id,
                            )
                import sys
                print(f"[DRAIN] {bash_id} POST-LOOP, shell.status={shell.status}", file=sys.stderr)
                # Reap the exit code.
                try:
                    returncode = await process.wait()
                    import sys
                    print(f"[DRAIN] {bash_id} wait() returned {returncode} status={shell.status}", file=sys.stderr)
                except Exception as e:
                    import sys
                    print(f"[DRAIN] {bash_id} wait() raised {e!r}", file=sys.stderr)
                    returncode = -1
                # Only update status if the kill branch
                # hasn't already done it — ``update_status``
                # derives the label from exit_code
                # (completed / failed) and would clobber
                # the "terminated" label the kill branch
                # set.
                if shell.status == "running":
                    shell.update_status(
                        is_alive=False, exit_code=returncode,
                    )
                else:
                    # Kill branch already set status; just
                    # record the exit code so the LLM sees
                    # the signal number.
                    shell.exit_code = returncode
                    logger.debug(
                        "background-shell monitor: %s "
                        "kill-branch exit_code=%r",
                        bash_id, returncode,
                    )
                # Final flush so cross-worker readers see
                # every line we ever wrote, not just the
                # batched ones.
                if shells_book is not None and lines_book is not None:
                    try:
                        if shell.next_seq - 1 > shell.last_flushed_seq:
                            shells_book.bump_seq(
                                bash_id=bash_id,
                                new_last_seq=shell.next_seq - 1,
                            )
                            shell.last_flushed_seq = (
                                shell.next_seq - 1
                            )
                        shells_book.finalize(
                            bash_id=bash_id,
                            status=shell.status,
                            exit_code=shell.exit_code,
                        )
                    except Exception:
                        logger.exception(
                            "background-shell monitor: "
                            "finalize failed (bash_id=%s)",
                            bash_id,
                        )
            except Exception as e:
                shell.status = "error"
                shell.add_output(f"monitor error: {e}")
                if shells_book is not None:
                    try:
                        shells_book.finalize(
                            bash_id=bash_id,
                            status="error",
                            exit_code=-1,
                        )
                    except Exception:
                        logger.exception(
                            "background-shell monitor: "
                            "error-finalize failed "
                            "(bash_id=%s)",
                            bash_id,
                        )
            finally:
                # Always drop the monitor task handle so a
                # future ``terminate`` doesn't try to
                # cancel a finished coroutine.
                cls._monitor_tasks.pop(bash_id, None)

        cls._monitor_tasks[bash_id] = asyncio.create_task(_drain())

    # -- termination -----------------------------------------------------

    @classmethod
    async def terminate(
        cls,
        *,
        bash_id: str,
        bus: "NewBus | None",
    ) -> _BackgroundShell:
        """Same-worker kill: cancel monitor, SIGTERM/SIGKILL, evict.

        Cross-worker kill goes through :meth:`request_kill`
        instead — it doesn't touch the local subprocess (the
        caller doesn't own it). The owner monitor's
        kill-claim check picks up the request and ends the
        process.
        """
        shell = cls._shells.get(bash_id)
        if shell is None:
            raise ValueError(f"Shell not found: {bash_id}")
        # Stop the monitor first so it doesn't race with
        # our own process.wait() / process.terminate().
        monitor = cls._monitor_tasks.pop(bash_id, None)
        if monitor is not None and not monitor.done():
            monitor.cancel()
        await shell.terminate()
        # Final flush + row finalize so the post-kill state
        # is observable cross-worker.
        shells_book, _ = _books(bus)
        if shells_book is not None:
            try:
                shells_book.finalize(
                    bash_id=bash_id,
                    status=shell.status,
                    exit_code=shell.exit_code,
                )
            except Exception:
                logger.exception(
                    "background-shell terminate: row "
                    "finalize failed (bash_id=%s)",
                    bash_id,
                )
        cls._shells.pop(bash_id, None)
        return shell

    @classmethod
    def request_kill(
        cls,
        *,
        bash_id: str,
        bus: "NewBus | None",
    ) -> bool:
        """Cross-worker kill: flip ``kill_requested`` on the row.

        No-op (returns ``False``) when the row doesn't exist
        or no bus is wired. The owner's monitor task picks up
        the flag on its next loop iteration and ends the
        subprocess.
        """
        shells_book, _ = _books(bus)
        if shells_book is None:
            return False
        try:
            return shells_book.request_kill(bash_id=bash_id)
        except Exception:
            logger.exception(
                "background-shell registry: cross-worker "
                "kill request failed (bash_id=%s)",
                bash_id,
            )
            return False