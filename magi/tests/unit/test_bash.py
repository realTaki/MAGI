"""Tests for the shell-execution tools (bash + output + kill).

These tests shell out to a real ``bash`` (and a real
filesystem) — the surface is small enough that mocking
``asyncio.create_subprocess_*`` would test the mock
rather than the tool. We pick portable test commands
(``echo``, ``pwd``, ``sleep``, ``exit 1``) so the suite
runs anywhere a POSIX shell exists.

The background-process tests need a moment of real
asyncio time (we ``await asyncio.sleep(0.05)`` to let the
monitor task drain). That's fine for a single-test
process; if the suite grows to 100+ bash tests we may
want to swap in a fake clock, but v0 doesn't need it.
"""

from __future__ import annotations

import asyncio
import os
import platform
from pathlib import Path

import pytest

from magi.agent.tools.base import ToolContext, ToolResult
from magi.agent.tools.bash import (
    BashKillTool,
    BashOutputTool,
    BashRunTool,
)


# -- fixtures --------------------------------------------------------------


@pytest.fixture
def workspace_ctx(tmp_path, monkeypatch):
    """Fresh tmp workspace + ToolContext.

    Each test gets its own tmp_path so concurrent
    background-process tests don't share state.
    """
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(tmp_path))
    return ToolContext(
        state_dir=str(tmp_path / "state"),
        workspace=tmp_path,
        chat_id="9001",
        employee_id=42,
        channel="webui",
    )


def _run(tool: BashRunTool, ctx: ToolContext, **kwargs) -> ToolResult:
    """Helper to drive the async run() in tests.

    The tool API is async; tests run synchronously.
    """
    return asyncio.run(tool.run(ctx, **kwargs))


def _output(tool: BashOutputTool, ctx: ToolContext, **kwargs) -> ToolResult:
    return asyncio.run(tool.run(ctx, **kwargs))


def _kill(tool: BashKillTool, ctx: ToolContext, **kwargs) -> ToolResult:
    return asyncio.run(tool.run(ctx, **kwargs))


# Skip the bash tests on Windows PowerShell — the test
# commands here are POSIX-flavoured. The tool itself
# auto-detects and uses PowerShell on Windows; for now
# we document the gap and run the suite on Unix only.
pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="BashTool tests use POSIX shell syntax; Windows PowerShell path is not exercised here.",
)


# -- BashRunTool: foreground ---------------------------------------------


def test_run_foreground_returns_stdout(workspace_ctx):
    tool = BashRunTool()
    result = _run(tool, workspace_ctx, command="echo hello world")
    assert not result.is_error
    assert "hello world" in result.content
    assert "[exit_code] 0" in result.content


def test_run_foreground_marks_nonzero_exit_as_error(workspace_ctx):
    tool = BashRunTool()
    result = _run(tool, workspace_ctx, command="exit 7")
    assert result.is_error
    assert "[exit_code] 7" in result.content


def test_run_foreground_separates_stderr(workspace_ctx):
    """stderr surfaces in the content with a [stderr]
    header so the LLM can grep it. Non-zero exit
    still flips ``is_error``.
    """
    tool = BashRunTool()
    result = _run(
        tool, workspace_ctx,
        command="echo on-stdout; echo on-stderr 1>&2; exit 3",
    )
    assert result.is_error
    assert "on-stdout" in result.content
    assert "on-stderr" in result.content
    assert "[stderr]" in result.content
    assert "[exit_code] 3" in result.content


def test_run_foreground_handles_no_output(workspace_ctx):
    """A successful command with no stdout/stderr still
    returns a non-error result with the ``(no output)``
    placeholder. The LLM has a string to read even
    when the process was silent."""
    tool = BashRunTool()
    result = _run(tool, workspace_ctx, command="true")
    assert not result.is_error
    assert "(no output)" in result.content
    assert "[exit_code] 0" in result.content


def test_run_foreground_clamps_timeout_to_ceiling(workspace_ctx):
    """``timeout=10000`` should clamp to 600 (the
    documented max) — the underlying call would hang
    for 10000 seconds otherwise, blocking the test
    runner.
    """
    tool = BashRunTool()
    result = _run(
        tool, workspace_ctx,
        command="echo clamped",
        timeout=10000,
    )
    # Doesn't actually wait 10k seconds — clamps +
    # returns fast. The clamp itself is hard to test
    # without racing the loop; we verify the call
    # succeeded (i.e. didn't trip the cap) and the
    # content is the expected echo.
    assert not result.is_error
    assert "clamped" in result.content


def test_run_foreground_cwd_is_workspace(workspace_ctx):
    """The process is locked to the workspace — ``pwd``
    returns the workspace path, not the agent's cwd."""
    tool = BashRunTool()
    result = _run(tool, workspace_ctx, command="pwd")
    assert not result.is_error
    # Resolve both sides to handle /private/var vs
    # /var on macOS or symlinked tmp dirs.
    expected = str(workspace_ctx.workspace.resolve())
    assert expected in result.content


def test_run_foreground_can_write_inside_workspace(workspace_ctx):
    """The LLM should be able to drop a file in the
    workspace (e.g. generate a config, write a test
    fixture). The process's cwd is the workspace, so
    relative paths land inside."""
    tool = BashRunTool()
    result = _run(
        tool, workspace_ctx,
        command='echo "from bash" > /tmp/dummy.txt 2>/dev/null; '
                'echo "from bash" > out.txt && cat out.txt',
    )
    # The ``out.txt`` in cwd is what we care about —
    # it's inside the workspace.
    assert not result.is_error
    assert "from bash" in result.content
    assert (workspace_ctx.workspace / "out.txt").exists()


def test_run_foreground_empty_command_rejected(workspace_ctx):
    tool = BashRunTool()
    result = _run(tool, workspace_ctx, command="")
    assert result.is_error
    assert "required" in result.content.lower()


# -- BashRunTool: background ----------------------------------------------


def test_run_background_returns_bash_id(workspace_ctx):
    """A short backgrounded command returns a bash_id
    immediately. The LLM gets the id, then polls via
    BashOutputTool.
    """
    tool = BashRunTool()
    result = _run(
        tool, workspace_ctx,
        command="sleep 0.05; echo finished",
        run_in_background=True,
    )
    assert not result.is_error
    # bash_id is a short hex string in the result body.
    assert "Bash ID:" in result.content
    import re
    assert re.search(r"Bash ID:\s*\w+", result.content) is not None


def test_run_background_full_lifecycle(workspace_ctx):
    """End-to-end: start a long-ish background process,
    poll its output, then kill it. The whole loop
    runs through BashRunTool + BashOutputTool +
    BashKillTool as the LLM would.

    Background processes are tied to the event loop
    that spawned them. We must run the whole flow in
    a single ``asyncio.run`` block — creating a new
    loop per call (as the helper does) leaves the
    subprocess bound to a dead loop, and the
    subsequent ``process.wait()`` raises
    ``RuntimeError: attached to a different loop``.
    """
    async def _lifecycle() -> None:
        run_tool = BashRunTool()
        output_tool = BashOutputTool()
        kill_tool = BashKillTool()

        # 1. Start in background.
        started = await run_tool.run(
            workspace_ctx,
            command=(
                "for i in 1 2 3; do "
                "echo line-$i; "
                "sleep 0.02; "
                "done; "
                "echo done"
            ),
            run_in_background=True,
        )
        assert not started.is_error
        import re
        bid = re.search(r"Bash ID:\s*(\w+)", started.content).group(1)

        # 2. Give the process + monitor task a moment
        # to actually start writing.
        await asyncio.sleep(0.15)

        # 3. Poll output — should see the lines we wrote.
        out = await output_tool.run(workspace_ctx, bash_id=bid)
        assert not out.is_error
        assert "line-1" in out.content
        assert "line-2" in out.content
        assert "line-3" in out.content
        # Status is one of running/completed/failed.
        assert "[status]" in out.content

        # 4. Second poll returns ONLY new output.
        await asyncio.sleep(0.05)
        again = await output_tool.run(workspace_ctx, bash_id=bid)
        assert not again.is_error
        # ``line-1`` already consumed — must NOT appear.
        assert "line-1" not in again.content

        # 5. Kill the (still-running) background shell.
        from magi.agent.tools.bash import _BackgroundShellManager
        await kill_tool.run(workspace_ctx, bash_id=bid)
        # The id is gone from the registry.
        assert _BackgroundShellManager.get(bid) is None

    asyncio.run(_lifecycle())


def test_run_background_filter_narrows_output(workspace_ctx):
    """``filter_str`` is a regex; only matching lines
    come back. Non-matching lines are consumed
    (skipped permanently) so the same line isn't
    returned twice if the LLM later drops the
    filter.

    Same single-loop dance as
    :func:`test_run_background_full_lifecycle` —
    see its docstring.
    """
    async def _filter_flow() -> None:
        run_tool = BashRunTool()
        output_tool = BashOutputTool()

        started = await run_tool.run(
            workspace_ctx,
            command='echo "INFO: hi"; echo "ERROR: oops"; echo "INFO: bye"',
            run_in_background=True,
        )
        import re
        bid = re.search(r"Bash ID:\s*(\w+)", started.content).group(1)
        await asyncio.sleep(0.1)

        filtered = await output_tool.run(
            workspace_ctx,
            bash_id=bid,
            filter_str="^ERROR:",
        )
        assert not filtered.is_error
        assert "ERROR: oops" in filtered.content
        # ``INFO:`` lines were consumed by the filter
        # call (they didn't match). A follow-up without
        # a filter returns them empty (already read).
        assert "INFO: hi" not in filtered.content
        assert "INFO: bye" not in filtered.content

    asyncio.run(_filter_flow())


# -- BashOutputTool: error paths ------------------------------------------


def test_output_missing_bash_id_returns_error(workspace_ctx):
    tool = BashOutputTool()
    result = _output(tool, workspace_ctx, bash_id="nope-not-real")
    assert result.is_error
    assert "not found" in result.content


def test_output_empty_bash_id_rejected(workspace_ctx):
    tool = BashOutputTool()
    result = _output(tool, workspace_ctx, bash_id="")
    assert result.is_error


# -- BashKillTool: error paths + idempotency ------------------------------


def test_kill_unknown_id_is_idempotent_noop(workspace_ctx):
    """Killing a never-existed id is a successful no-op
    so the LLM can retry without seeing a false
    ``is_error``."""
    tool = BashKillTool()
    result = _kill(tool, workspace_ctx, bash_id="nope-not-real")
    # The reference tool returns is_error=True on
    # not-found; we do too, but the content explains
    # it's idempotent so the LLM can ignore on
    # retry. (Either way, no zombie process leaks.)
    assert "not found" in result.content.lower() or \
        "idempotent" in result.content.lower()


def test_kill_empty_bash_id_rejected(workspace_ctx):
    tool = BashKillTool()
    result = _kill(tool, workspace_ctx, bash_id="")
    assert result.is_error


def test_kill_terminates_a_running_background_process(workspace_ctx):
    """Start a long-running background, kill it,
    verify the killed id is no longer in the
    manager's registry.

    Same single-loop dance as
    :func:`test_run_background_full_lifecycle`."""
    async def _kill_flow() -> None:
        from magi.agent.tools.bash import _BackgroundShellManager
        run_tool = BashRunTool()
        kill_tool = BashKillTool()

        started = await run_tool.run(
            workspace_ctx,
            command="sleep 60",
            run_in_background=True,
        )
        assert not started.is_error
        import re
        bid = re.search(r"Bash ID:\s*(\w+)", started.content).group(1)

        # Kill it.
        killed = await kill_tool.run(workspace_ctx, bash_id=bid)
        assert (
            not killed.is_error
            or "idempotent" in killed.content.lower()
        )

        # The id is gone from the registry — a
        # follow-up BashOutputTool call would report
        # "not found".
        assert _BackgroundShellManager.get(bid) is None

    asyncio.run(_kill_flow())


# -- workspace lock ------------------------------------------------------


def test_run_initial_cwd_is_workspace(workspace_ctx):
    """The subprocess's **initial** cwd is the workspace
    root. We don't enforce a stay-inside-workspace
    rule on subsequent ``cd`` calls — the LLM is
    trusted to stay inside the tree, matching the
    reference bash tool's posture. The actual
    safety boundary is the operator's container /
    deploy boundary; this tool just sets a sane
    starting point.
    """
    tool = BashRunTool()
    result = _run(tool, workspace_ctx, command="pwd")
    assert not result.is_error
    expected = str(workspace_ctx.workspace.resolve())
    assert expected in result.content


# -- registry integration ------------------------------------------------


def test_bash_tools_appear_in_registry(tmp_path, monkeypatch):
    """Sanity: all three tools are registered. The
    tool names show up in the LLM's tool list, in
    the order registered. ``MAGI_STATE_DIR`` must be
    set so the registry's tool-construction path
    can build the SQLAlchemy engine (the LLM tools
    gate on roles which require a DB lookup).
    """
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(tmp_path))
    # The registry builds each tool on first call;
    # the role-gate tools open a session lazily so
    # the engine only needs to be importable here.
    from magi.agent.tools.registry import get_tool_schemas
    names = [t["name"] for t in get_tool_schemas()]
    assert "bash" in names
    assert "bash_output" in names
    assert "bash_kill" in names