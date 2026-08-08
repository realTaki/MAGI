"""Cross-worker tests for the shell-execution tools.

These verify the cross-worker visibility added when the
in-memory ``_BackgroundShellManager`` got paired with the
new_bus :class:`BackgroundShellBook` tables: a shell
spawned by ``worker A`` is readable / killable from
``worker B`` without either side having access to the live
``asyncio.subprocess.Process`` handle.

Two ``ToolContext`` objects share the same ``NewBus``
(SQLite-backed) but carry distinct ``worker_id`` fields.
The subprocess is bound to whichever process is running
the test's event loop — but the *manager* treats the two
contexts as separate workers because ``worker_id``
differs. That mirrors the real production shape: one
process, two ``ToolsWorker`` instances routed by a load
balancer.
"""

from __future__ import annotations

import asyncio
import platform
import re

import pytest

from magi.new_bus import NewBus, bootstrap_new_bus
from magi.tools.base import ToolContext
from magi.tools.shell.kill import BashKillTool
from magi.tools.shell.output import BashOutputTool
from magi.tools.shell.run import BashRunTool


pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="Cross-worker bash tests use POSIX shell syntax; Windows PowerShell path is not exercised here.",
)


# -- fixtures --------------------------------------------------------------


@pytest.fixture
def shared_bus(tmp_path):
    """Per-test SQLite-backed :class:`NewBus` + a seeded contact.

    ``background_shells.uid`` has a FK to ``contacts.id``;
    the contact is auto-incremented so we can't hard-code
    its id. Returns a small holder exposing both ``bus``
    and ``uid`` so callers can wire ``ToolContext`` without
    re-querying.
    """
    from types import SimpleNamespace
    bus = bootstrap_new_bus(state_dir=str(tmp_path))
    bus._local_factory.create_all()
    contact = bus.contacts_book.add(
        name="cross-worker-test", role="assigned",
    )
    return SimpleNamespace(bus=bus, uid=contact.id)


@pytest.fixture
def worker_a_ctx(tmp_path, shared_bus):
    """``ToolContext`` for worker A — the spawning side."""
    return ToolContext(
        workspace=tmp_path,
        uid=shared_bus.uid,
        channel="webui",
        bus=shared_bus.bus,
        worker_id="tools-worker-aaaa",
    )


@pytest.fixture
def worker_b_ctx(tmp_path, shared_bus):
    """``ToolContext`` for worker B — the reading / killing side.

    Distinct ``worker_id`` from A; same ``bus`` (same SQLite
    file) — so they're "two processes in the fleet" as far
    as the manager is concerned.
    """
    return ToolContext(
        workspace=tmp_path,
        uid=shared_bus.uid,
        channel="webui",
        bus=shared_bus.bus,
        worker_id="tools-worker-bbbb",
    )


def _bid(content: str) -> str:
    """Extract bash_id from a ``bash`` tool result body."""
    return re.search(r"Bash ID:\s*(\w+)", content).group(1)


@pytest.fixture(autouse=True)
def _set_workspace_env(monkeypatch, tmp_path):
    """The ToolCatalog publish path runs every builtin tool's
    constructor; :class:`SkillLoaderTool` reads
    ``MAGI_WORKSPACE_DIR`` at construction time. Tests that
    go through ``ToolsWorker.start`` need it set even when
    the actual bash tests don't (they construct tools
    individually)."""
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(tmp_path / "state"))


# -- cross-process simulation ---------------------------------------------
#
# In production, worker A and worker B live in two separate
# Python processes; their ``_BackgroundShellManager._shells``
# class-level dicts are independent — A's local entries are
# invisible to B, and vice versa. Tests run everything in one
# process, so to faithfully exercise the cross-worker paths we
# have to swap out the class attribute around calls. This
# context manager snapshots the dict, replaces it with a fresh
# one, and restores on exit. Bash ids whose ``owner_worker_id``
# matches ``as_worker_id`` are pre-seeded so the "I'm the owner"
# branch can also be exercised if a test wants it.
#
# By default (``as_worker_id=None``) the swap leaves the dict
# empty — simulating a sibling worker that has never seen any
# bash_ids. That's the typical cross-worker shape.

from contextlib import contextmanager
from typing import Iterator


@contextmanager
def _as_sibling_worker(bus) -> Iterator[None]:
    """Run a block as if it were a different worker process.

    Swaps out ``_BackgroundShellManager._shells`` (and the
    related class-level dicts) so the manager's "local"
    lookup returns ``None`` for shells owned by anyone
    other than the test's caller. The DB-backed path then
    has to serve every read / kill, which is exactly the
    production semantic.

    The DB itself isn't touched — the shell row + line rows
    stay where they are; only the in-process cache is
    temporarily emptied.
    """
    from magi.tools.shell import _manager as mgr

    saved_shells = mgr._BackgroundShellManager._shells
    saved_monitors = mgr._BackgroundShellManager._monitor_tasks
    saved_consumed = mgr._BackgroundShellManager._consumed_seq
    mgr._BackgroundShellManager._shells = {}
    mgr._BackgroundShellManager._monitor_tasks = {}
    mgr._BackgroundShellManager._consumed_seq = {}
    try:
        yield
    finally:
        mgr._BackgroundShellManager._shells = saved_shells
        mgr._BackgroundShellManager._monitor_tasks = saved_monitors
        mgr._BackgroundShellManager._consumed_seq = saved_consumed


# -- same-worker (sanity check that bus=None path is intact) --------------


def test_local_path_still_works_without_bus(tmp_path, monkeypatch):
    """The bus=None path is the pre-existing single-worker
    behaviour. We don't want to silently regress v0 in the
    cross-worker refactor."""
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(tmp_path / "state"))
    ctx = ToolContext(
        workspace=tmp_path, uid=42, channel="webui",
        # bus=None on purpose
    )
    run_tool = BashRunTool()
    out_tool = BashOutputTool()

    async def _flow():
        started = await run_tool.run(
            ctx,
            command="echo cross-worker-local; echo done",
            run_in_background=True,
        )
        assert not started.is_error
        bid = _bid(started.content)

        # Let the monitor drain.
        await asyncio.sleep(0.15)

        out = await out_tool.run(ctx, bash_id=bid)
        assert not out.is_error
        assert "cross-worker-local" in out.content

        # Clean up — kills the local process.
        kill_tool = BashKillTool()
        await kill_tool.run(ctx, bash_id=bid)

    asyncio.run(_flow())


# -- cross-worker: read --------------------------------------------------


def test_cross_worker_output_visible_to_sibling(worker_a_ctx, worker_b_ctx):
    """Worker A starts a shell; worker B reads its output via
    the line Book.

    The local ring buffer on B is empty — B's
    ``_BackgroundShellManager.get`` returns a
    :class:`_CrossWorkerView`, which the tool then reads from
    ``background_shell_lines``.
    """
    run_tool = BashRunTool()
    out_tool = BashOutputTool()

    async def _flow():
        started = await run_tool.run(
            worker_a_ctx,
            command=(
                "echo cross-line-1; "
                "echo cross-line-2; "
                "echo cross-line-3"
            ),
            run_in_background=True,
        )
        assert not started.is_error
        bid = _bid(started.content)

        # Let the monitor task drain + flush a batch.
        await asyncio.sleep(0.2)

        # Read from worker B. The sibling-worker swap makes
        # this look up the DB row + line table instead of
        # the in-memory buffer that worker A populated.
        with _as_sibling_worker(worker_b_ctx.bus):
            out = await out_tool.run(worker_b_ctx, bash_id=bid)
        assert not out.is_error
        assert "cross-line-1" in out.content
        assert "cross-line-2" in out.content
        assert "cross-line-3" in out.content
        # Status line surfaces the owner worker id so the
        # LLM can tell whose shell it's looking at.
        assert "tools-worker-aaaa" in out.content

        # Clean up — kill from the owner side so the
        # process actually ends.
        kill_tool = BashKillTool()
        await kill_tool.run(worker_a_ctx, bash_id=bid)

    asyncio.run(_flow())


def test_cross_worker_filter_consumes_lines(worker_a_ctx, worker_b_ctx):
    """``filter_str`` on a cross-worker read consumes
    non-matching lines via the line Book's ``seq`` cursor."""
    run_tool = BashRunTool()
    out_tool = BashOutputTool()

    async def _flow():
        started = await run_tool.run(
            worker_a_ctx,
            command='echo "INFO: noise-1"; echo "ERROR: real"; echo "INFO: noise-2"',
            run_in_background=True,
        )
        bid = _bid(started.content)
        await asyncio.sleep(0.2)

        # Worker B reads with an ``ERROR:`` filter.
        with _as_sibling_worker(worker_b_ctx.bus):
            filtered = await out_tool.run(
                worker_b_ctx, bash_id=bid, filter_str="^ERROR:",
            )
        assert not filtered.is_error
        assert "ERROR: real" in filtered.content
        assert "INFO: noise-1" not in filtered.content
        assert "INFO: noise-2" not in filtered.content

        # Follow-up without filter returns nothing — the
        # noise lines were consumed (cursor advanced past
        # them).
        with _as_sibling_worker(worker_b_ctx.bus):
            plain = await out_tool.run(worker_b_ctx, bash_id=bid)
        assert not plain.is_error
        assert "INFO: noise-1" not in plain.content
        assert "INFO: noise-2" not in plain.content
        assert "ERROR: real" not in plain.content

        # Cleanup.
        await BashKillTool().run(worker_a_ctx, bash_id=bid)

    asyncio.run(_flow())


# -- cross-worker: kill --------------------------------------------------


def test_cross_worker_kill_ends_owner_process(worker_a_ctx, worker_b_ctx):
    """Worker B's ``bash_kill`` flips ``kill_requested=1``
    on the row; worker A's monitor picks it up on its next
    loop iteration and terminates the subprocess."""
    run_tool = BashRunTool()
    kill_tool = BashKillTool()
    out_tool = BashOutputTool()

    async def _flow():
        started = await run_tool.run(
            worker_a_ctx,
            command="sleep 60; echo should-never-print",
            run_in_background=True,
        )
        bid = _bid(started.content)

        # Give the monitor task a moment to enter its loop
        # and start polling ``kill_requested``.
        await asyncio.sleep(0.15)

        # Kill from worker B. The sibling-worker swap is
        # critical here — without it, the manager would
        # think B owns the subprocess and try to terminate
        # it locally, which doesn't exist in B's process.
        with _as_sibling_worker(worker_b_ctx.bus):
            killed = await kill_tool.run(worker_b_ctx, bash_id=bid)
        assert not killed.is_error
        assert "Kill requested" in killed.content
        assert "tools-worker-aaaa" in killed.content

        # Wait for the owner's monitor to react + finalize.
        # 1s is plenty for a single UPDATE round-trip +
        # SIGTERM.
        out_content = ""
        terminal = False
        for _ in range(20):
            await asyncio.sleep(0.05)
            with _as_sibling_worker(worker_b_ctx.bus):
                out = await out_tool.run(worker_b_ctx, bash_id=bid)
            out_content = out.content
            if "terminated" in out_content or "failed" in out_content:
                terminal = True
                break
        if not terminal:
            pytest.fail(
                f"shell didn't reach terminal status; "
                f"last content: {out_content!r}"
            )

        # The shell's owner-row should now reflect
        # ``status='terminated'`` (or ``failed`` depending
        # on whether SIGTERM landed before the wait).
        view = worker_b_ctx.bus.background_shells_book.get(bash_id=bid)
        assert view is not None
        assert view.status in ("terminated", "failed")

    asyncio.run(_flow())


def test_cross_worker_kill_is_idempotent(worker_a_ctx, worker_b_ctx):
    """A second ``bash_kill`` on a cross-worker shell that
    has already been terminated is a successful no-op."""
    run_tool = BashRunTool()
    kill_tool = BashKillTool()

    async def _flow():
        started = await run_tool.run(
            worker_a_ctx,
            command="echo hi; exit 0",
            run_in_background=True,
        )
        bid = _bid(started.content)
        await asyncio.sleep(0.2)

        # First kill (from B).
        with _as_sibling_worker(worker_b_ctx.bus):
            await kill_tool.run(worker_b_ctx, bash_id=bid)
        # Wait for owner to finalize.
        for _ in range(20):
            await asyncio.sleep(0.05)
            view = worker_b_ctx.bus.background_shells_book.get(bash_id=bid)
            if view is not None and view.status != "running":
                break

        # Second kill (from B) — row still exists; flag
        # flips again, monitor has already exited so this
        # is essentially a no-op.
        with _as_sibling_worker(worker_b_ctx.bus):
            again = await kill_tool.run(worker_b_ctx, bash_id=bid)
        assert "idempotent" in again.content.lower() or \
            not again.is_error

    asyncio.run(_flow())


# -- owner restart → orphan ----------------------------------------------


def test_owner_restart_marks_running_shells_orphaned(
    tmp_path, shared_bus, worker_a_ctx,
):
    """When a worker starts up, shells it used to own that
    are still ``status='running'`` are reaped as
    ``orphaned`` — the live subprocess is gone.

    Simulates the restart by inserting a row directly via
    the Book (no actual subprocess — just the persistent
    side of the story) and then calling the worker's
    ``_reap_orphaned_shells`` to verify it picks the row
    up.
    """
    # Plant a row as if a previous run of worker A had
    # left it running.
    shared_bus.bus.background_shells_book.add(
        bash_id="deadbeef",
        command="sleep 60",
        owner_worker_id=worker_a_ctx.worker_id,
        uid=worker_a_ctx.uid,
    )

    # Sanity: row is ``running``.
    row = shared_bus.bus.background_shells_book.get(bash_id="deadbeef")
    assert row.status == "running"

    # Re-run the worker's startup reaper. We can't easily
    # construct a full :class:`ToolsWorker` without
    # standing up an LLM stack, so we call the helper
    # directly — it's the same code path the worker runs
    # at ``start()``.
    from magi.tools.worker import ToolsWorker

    class _ProbeWorker(ToolsWorker):
        """A ToolsWorker stripped of its ``_run`` loop so
        we can drive ``start()`` without an event loop
        going forever."""

        def __init__(self, bus: NewBus):
            # Bypass ``__init__``'s asyncio bits — we only
            # want the reaper.
            self.bus = bus
            self.worker_id = worker_a_ctx.worker_id
            self.poll_seconds = 0.25
            self._task = None
            self._stopping = False

        async def start(self) -> None:
            # Run the reaper only — skip the catalog
            # publish (it triggers tool construction,
            # which reads ``MAGI_WORKSPACE_DIR`` and pulls
            # in unrelated dependencies we don't want in
            # this test). The reaper is the unit under
            # test here.
            await asyncio.to_thread(self._reap_orphaned_shells)
            self._stopping = True

    asyncio.run(_ProbeWorker(shared_bus.bus).start())

    # Row should now be ``orphaned``.
    row = shared_bus.bus.background_shells_book.get(bash_id="deadbeef")
    assert row.status == "orphaned"
    assert row.exit_code is not None


def test_owner_restart_does_not_touch_other_workers_shells(
    tmp_path, shared_bus,
):
    """The reaper is per-worker — worker A's startup
    doesn't reap worker C's running shells."""
    shared_bus.bus.background_shells_book.add(
        bash_id="cafe1234",
        command="sleep 60",
        owner_worker_id="tools-worker-cccc",  # different owner
        uid=shared_bus.uid,
    )

    from magi.tools.worker import ToolsWorker

    class _ProbeWorker(ToolsWorker):
        def __init__(self, bus: NewBus):
            self.bus = bus
            self.worker_id = "tools-worker-aaaa"
            self.poll_seconds = 0.25
            self._task = None
            self._stopping = False

        async def start(self) -> None:
            await asyncio.to_thread(self._reap_orphaned_shells)
            self._stopping = True

    asyncio.run(_ProbeWorker(shared_bus.bus).start())

    # Worker C's shell is untouched.
    row = shared_bus.bus.background_shells_book.get(bash_id="cafe1234")
    assert row.status == "running"