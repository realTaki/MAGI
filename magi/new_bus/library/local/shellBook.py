"""BackgroundShellBook + BackgroundShellLineBook — cross-worker shell registry.

Two tables that together solve the multi-worker visibility gap
:class:`magi.tools.shell._manager._BackgroundShellManager` had when the
:class:`~magi.tools.worker.ToolsWorker` was instantiated per-process:

- ``background_shells``     — one row per background shell. Carries
                              the immutable metadata (``bash_id`` /
                              ``command`` / ``owner_worker_id`` /
                              ``uid``) plus the eventual-consistency
                              state (``status`` / ``exit_code`` /
                              ``started_at`` / ``ended_at``) and the
                              cross-worker kill flag
                              (``kill_requested``).
- ``background_shell_lines`` — one row per stdout line the owner
                              worker drained. Lets a non-owner worker's
                              :class:`~magi.tools.shell.output.BashOutputTool`
                              read output without going through the
                              spawning process.

Why two tables, not one
-----------------------

The shell row is small (one row per shell, ~hundreds of bytes), updated
sparsely (``status`` / ``exit_code`` / ``kill_requested`` flip a few
times across the shell's life). The line table is append-heavy — a
chatty process (``npm install``, ``pytest -v``) can produce thousands
of rows. Splitting keeps the shell row's index cheap even when the
line table grows, and lets a future retention job age out lines
without touching shell rows.

Why a separate line table and not a JSON column on the shell row
----------------------------------------------------------------

A ``Text`` JSON column would force the owner monitor to rewrite the
*entire* buffer on every drain — O(n²) writes for n lines. A dedicated
table appends one row per line and reads use ``seq > ?`` with the row's
``last_seq`` as the high-water mark. The two-table shape is also what
``tasksBook`` / ``task_runs`` uses for the same reason (definition rows
vs execution traces).

Schema mirrors the old bus's background-shell bookkeeping (the registry
on ``_BackgroundShellManager``) so the v0 migration to new_bus is a
mechanical rename of where state lives, not a behavioural change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.library.base import BaseBook


# Status values mirrored from the old in-memory
# ``_BackgroundShell.status`` field so the wire shape stays identical
# for callers that already pattern-match on it.
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_TERMINATED = "terminated"
STATUS_ERROR = "error"
STATUS_ORPHANED = "orphaned"

ALL_STATUSES = frozenset({
    STATUS_RUNNING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_TERMINATED,
    STATUS_ERROR,
    STATUS_ORPHANED,
})

# Bash id length mirrors the in-memory manager's
# ``_BASH_ID_LEN``. Kept as a module constant so the ORM column width
# and the manager's uuid slice agree (the migration landed together
# — changing one without the other would silently truncate).
BASH_ID_LEN = 8


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class BackgroundShell:
    """One background shell's persistent state.

    The ``owner_worker_id`` is the
    :attr:`~magi.tools.worker.ToolsWorker.worker_id` of the process
    that spawned the subprocess — the only process that holds the
    live ``asyncio.subprocess.Process`` handle. Cross-worker readers
    consult this field to decide whether they can drain the in-memory
    buffer (same-worker) or must read from
    :class:`BackgroundShellLineBook` (cross-worker).

    ``kill_requested`` flips to ``1`` when a non-owner worker calls
    :class:`~magi.tools.shell.kill.BashKillTool`; the owner's monitor
    task polls it and terminates the subprocess on the next loop
    iteration. Atomic flip via a separate column (not a status value)
    so the cross-worker kill signal doesn't race with the owner's own
    status transitions.

    ``last_seq`` is the high-water mark of the line table — the
    number of lines the owner has flushed so far. Non-owner readers
    use it as the upper bound of an incremental read.

    ``command`` and ``uid`` are immutable post-insert (no UPDATE path
    on the Book touches them); they're recorded for audit / cleanup.
    """

    bash_id: str
    command: str
    owner_worker_id: str
    uid: int
    status: str = STATUS_RUNNING
    exit_code: Optional[int] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    kill_requested: int = 0
    last_seq: int = 0


@dataclass(frozen=True, slots=True)
class BackgroundShellLine:
    """One stdout line from a background shell.

    ``seq`` is the per-shell monotonic counter the owner monitor
    stamps on each insert. Cross-worker readers filter with
    ``seq > consumed_seq`` to get incremental output without the
    in-process buffer.

    Kept as a separate Book (rather than a method on
    :class:`BackgroundShellBook`) for symmetry with
    ``tasksBook`` / ``task_runs`` — same shape, same rationale (the
    line table has a different access pattern than the shell row).
    """

    id: int
    bash_id: str
    seq: int
    line: str
    created_at: Optional[datetime] = None


# -- internal ORM --------------------------------------------------------


class _BackgroundShellRow(Base):
    __tablename__ = "background_shells"

    bash_id: Mapped[str] = mapped_column(String(BASH_ID_LEN), primary_key=True)
    command: Mapped[str] = mapped_column(Text, nullable=False)
    owner_worker_id: Mapped[str] = mapped_column(String(64), nullable=False)
    uid: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=STATUS_RUNNING,
    )
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False,
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    kill_requested: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    last_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )

    __table_args__ = (
        Index("ix_background_shells_owner_status", "owner_worker_id", "status"),
        Index("ix_background_shells_uid", "uid"),
    )


class _BackgroundShellLineRow(Base):
    __tablename__ = "background_shell_lines"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    bash_id: Mapped[str] = mapped_column(
        ForeignKey("background_shells.bash_id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    line: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False,
    )

    __table_args__ = (
        # The hot read path is ``WHERE bash_id = ? AND seq > ? ORDER
        # BY seq LIMIT N`` — composite index covers both filter and
        # ordering without a separate sort step.
        Index("ix_background_shell_lines_bash_seq", "bash_id", "seq"),
    )


# -- Books ---------------------------------------------------------------


class BackgroundShellBook(BaseBook[_BackgroundShellRow, BackgroundShell]):
    """CRUD over the cross-worker background-shell registry.

    The Book owns every state transition a shell can go through
    (start → running; terminal → completed / failed / terminated /
    error / orphaned; cross-worker kill → ``kill_requested=1``).
    Callers never UPDATE the row directly — they go through these
    methods so the invariants (status + exit_code consistency,
    ``kill_requested`` semantics, ``last_seq`` monotonicity) stay
    in one place.
    """

    model_cls = _BackgroundShellRow
    dto_cls = BackgroundShell

    def get(self, *, bash_id: str) -> BackgroundShell | None:
        with self._session() as s:
            row = s.scalar(
                select(_BackgroundShellRow).where(
                    _BackgroundShellRow.bash_id == bash_id,
                )
            )
            return self._row_to_dto(row) if row else None

    def list_by_owner(
        self, *, owner_worker_id: str, status: str | None = None,
    ) -> list[BackgroundShell]:
        """Enumerate shells owned by ``owner_worker_id``.

        Used by the per-worker orphan-scan at
        :meth:`magi.tools.worker.ToolsWorker.start` — pass
        ``status=STATUS_RUNNING`` to find shells that might be
        orphaned by a previous run of this worker.
        """
        with self._session() as s:
            stmt = select(_BackgroundShellRow).where(
                _BackgroundShellRow.owner_worker_id == owner_worker_id,
            )
            if status is not None:
                stmt = stmt.where(_BackgroundShellRow.status == status)
            rows = s.scalars(stmt).all()
            return [self._row_to_dto(r) for r in rows]

    def add(
        self,
        *,
        bash_id: str,
        command: str,
        owner_worker_id: str,
        uid: int,
    ) -> BackgroundShell:
        """Insert one shell row at start-of-life.

        All other fields take their defaults (``status='running'``,
        ``kill_requested=0``, ``last_seq=0``, ``started_at=now``).
        ``bash_id`` is the caller-minted id (mirroring the in-memory
        manager's ``uuid.uuid4().hex[:BASH_ID_LEN]``).
        """
        with self._session() as s:
            row = _BackgroundShellRow(
                bash_id=bash_id,
                command=command,
                owner_worker_id=owner_worker_id,
                uid=uid,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def request_kill(self, *, bash_id: str) -> bool:
        """Set ``kill_requested=1`` for cross-worker termination.

        Returns ``True`` when the row existed (regardless of its
        current state — the owner's monitor will reconcile when it
        next checks the flag). Returns ``False`` when no row matches,
        so callers can distinguish "not found" from "already
        requested".

        Idempotent: a second call on the same shell also returns
        ``True``. The flag is a one-way edge; the owner resets state
        to ``terminated`` when it actually kills the process, but
        never resets ``kill_requested`` itself.
        """
        with self._session() as s:
            row = s.scalar(
                select(_BackgroundShellRow).where(
                    _BackgroundShellRow.bash_id == bash_id,
                )
            )
            if row is None:
                return False
            row.kill_requested = 1
            s.commit()
            return True

    def claim_kill_request(self, *, bash_id: str) -> bool:
        """Atomically read-and-clear ``kill_requested`` for the owner.

        The owner monitor calls this each loop iteration; returns
        ``True`` exactly once per flip, ``False`` otherwise. Used
        instead of a plain ``SELECT … WHERE kill_requested=1`` to
        avoid the owner killing the same shell twice if it processes
        the loop body slowly and the same kill is requested again
        before the shell's terminal status is written.

        Implemented with an UPDATE-then-rowcount return so the
        read-and-clear is a single round-trip even on SQLite.
        """
        from sqlalchemy import update
        with self._session() as s:
            result = s.execute(
                update(_BackgroundShellRow)
                .where(
                    _BackgroundShellRow.bash_id == bash_id,
                    _BackgroundShellRow.kill_requested == 1,
                )
                .values(kill_requested=0)
            )
            s.commit()
            return result.rowcount > 0

    def finalize(
        self,
        *,
        bash_id: str,
        status: str,
        exit_code: int | None,
    ) -> None:
        """Write terminal status + exit code + ended_at.

        ``status`` must be one of :data:`ALL_STATUSES` minus
        ``STATUS_RUNNING` (the Book refuses to finalize a shell
        as ``running`` — that's the start-of-life state).

        No-op when the row doesn't exist (the shell's been
        garbage-collected from this worker's local dict already,
        and a concurrent kill has done its own finalize).
        """
        if status not in ALL_STATUSES or status == STATUS_RUNNING:
            raise ValueError(
                f"finalize status must be a terminal value, got {status!r}"
            )
        with self._session() as s:
            row = s.scalar(
                select(_BackgroundShellRow).where(
                    _BackgroundShellRow.bash_id == bash_id,
                )
            )
            if row is None:
                return
            row.status = status
            row.exit_code = exit_code
            row.ended_at = utcnow_naive()
            s.commit()

    def mark_orphaned(self, *, bash_id: str, exit_code: int = -1) -> None:
        """Mark a shell as orphaned — owner worker can't find it.

        Called by :meth:`magi.tools.worker.ToolsWorker.start` for
        every ``status='running'`` shell the worker used to own but
        no longer has a live subprocess for (process died with the
        previous worker run). Cross-worker reads from then on see
        the terminal status instead of an indefinite "running".
        """
        self.finalize(
            bash_id=bash_id, status=STATUS_ORPHANED, exit_code=exit_code,
        )

    def bump_seq(self, *, bash_id: str, new_last_seq: int) -> None:
        """Advance the row's ``last_seq`` high-water mark.

        Called by the owner monitor after flushing one batch of lines
        so non-owner readers can size their incremental reads. Kept
        separate from line insertion (which writes to
        :class:`BackgroundShellLineBook`) so the two writes don't
        have to share a transaction — losing one ``bump_seq`` only
        delays cross-worker reads by one batch, not catastrophically.
        """
        with self._session() as s:
            row = s.scalar(
                select(_BackgroundShellRow).where(
                    _BackgroundShellRow.bash_id == bash_id,
                )
            )
            if row is None:
                return
            if new_last_seq > row.last_seq:
                row.last_seq = new_last_seq
                s.commit()


class BackgroundShellLineBook(BaseBook[_BackgroundShellLineRow, BackgroundShellLine]):
    """CRUD over the per-line stdout buffer.

    The owner monitor appends one row per drained line and the
    cross-worker :class:`~magi.tools.shell.output.BashOutputTool` reads
    incrementally. Rows are append-only from the API surface — there
    is no UPDATE path. The shell table's FK + ``ondelete='CASCADE'``
    cleans up lines when a shell row is deleted (manual cleanup, not
    yet wired).
    """

    model_cls = _BackgroundShellLineRow
    dto_cls = BackgroundShellLine

    def list_after(
        self, *, bash_id: str, after_seq: int, limit: int = 1000,
    ) -> list[BackgroundShellLine]:
        """Return lines with ``seq > after_seq`` for ``bash_id``.

        ``after_seq=0`` returns every line (the typical first-poll
        case where the cross-worker reader hasn't seen anything yet).
        ``limit`` caps the result so a chatty process can't dump
        tens of thousands of rows into a single LLM tool response —
        the caller (the bash_output tool) loops until it has either
        consumed everything or hit a sensible page size.
        """
        with self._session() as s:
            rows = s.scalars(
                select(_BackgroundShellLineRow)
                .where(
                    _BackgroundShellLineRow.bash_id == bash_id,
                    _BackgroundShellLineRow.seq > after_seq,
                )
                .order_by(_BackgroundShellLineRow.seq)
                .limit(limit)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def append(self, *, bash_id: str, seq: int, line: str) -> None:
        """Append one stdout line.

        ``seq`` is the caller-minted per-shell monotonic counter
        (typically ``row.last_seq + 1`` at call time). The Book
        trusts the caller to keep it monotonic; non-monotonic
        inserts would break incremental reads silently.
        """
        with self._session() as s:
            row = _BackgroundShellLineRow(
                bash_id=bash_id, seq=seq, line=line,
            )
            s.add(row)
            s.commit()


__all__ = [
    "ALL_STATUSES",
    "BASH_ID_LEN",
    "BackgroundShell",
    "BackgroundShellBook",
    "BackgroundShellLine",
    "BackgroundShellLineBook",
    "STATUS_COMPLETED",
    "STATUS_ERROR",
    "STATUS_FAILED",
    "STATUS_ORPHANED",
    "STATUS_RUNNING",
    "STATUS_TERMINATED",
]