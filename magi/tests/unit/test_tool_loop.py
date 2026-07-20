"""Tests for the D.16 tool-use loop + v0 tools.

Pinned behaviour:

  - ``ChatMessage`` accepts ``content_blocks``; ``ChatResult``
    carries ``stop_reason`` + ``tool_uses``.
  - ``MinimaxProvider.chat`` accepts a ``tools`` kwarg and
    forwards it to the SDK.
  - The ``safe_resolve`` helper rejects paths that escape
    the workspace (path traversal, absolute paths).
  - ``read_file`` / ``write_file`` / ``list_files`` work on
    real files in a tmp workspace; ``send_message`` returns
    an error on the webui channel.

The agent-loop integration (calling ``handle_message`` with
a mocked provider) is exercised by the live smoke (real chat
→ real provider → real loop). A pure-Python unit test of
the loop body would need to stub ``get_provider`` *and*
call ``handle_message`` directly, but ``test_tg_admin_routes``
patches ``agent_mod.handle_message = AsyncMock(...)`` without
``monkeypatch``, leaving an AsyncMock leak that survives
into later tests in the same session. Rather than fight
that, the agent-loop path is covered by:

  - live smoke (dev container restart + a real chat turn
    that exercises the tool loop end-to-end)
  - tool-level tests below (each tool's input validation,
    output shape, and security check)
  - schema tests (the wire format the SDK sees)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from magi.agent.llm.provider import ChatMessage, ChatResult
from magi.agent.tools._safe_path import safe_resolve
from magi.agent.tools.base import ToolContext, ToolResult
from magi.agent.tools.list_files import ListFilesTool
from magi.agent.tools.read_file import ReadFileTool
from magi.agent.tools.registry import (
    get_tool,
    get_tool_schemas,
)
from magi.agent.tools.send_message import SendMessageTool
from magi.agent.tools.write_file import WriteFileTool


# ────────────────────────────────────────────────────────────────── #
# Schema + provider wiring
# ────────────────────────────────────────────────────────────────── #


def test_chat_message_accepts_content_blocks():
    """A ``user`` message with ``tool_result`` blocks
    round-trips through the dataclass without losing the
    blocks. v0 only constructs these in the agent loop
    but a public dataclass should let any caller build
    them."""
    msg = ChatMessage(
        role="user",
        content="",
        content_blocks=[
            {"type": "tool_result", "tool_use_id": "abc",
             "content": "ok", "is_error": False},
        ],
    )
    assert msg.content_blocks is not None
    assert msg.content_blocks[0]["tool_use_id"] == "abc"


def test_chat_result_carries_stop_reason_and_tool_uses():
    """A assistant turn with ``tool_use`` blocks exposes
    them via ``ChatResult.tool_uses`` so the loop can
    dispatch without re-walking ``raw_blocks``."""
    result = ChatResult(
        text="",
        stop_reason="tool_use",
        tool_uses=[{"id": "x", "name": "read_file", "input": {"path": "SOUL.md"}}],
        raw_blocks=[
            {"type": "tool_use", "id": "x", "name": "read_file",
             "input": {"path": "SOUL.md"}},
        ],
    )
    assert result.stop_reason == "tool_use"
    assert result.tool_uses[0]["name"] == "read_file"


def test_minimax_provider_chat_signature_accepts_tools_kwarg():
    """``LLMProvider.chat`` signature includes ``tools`` (a
    list of Anthropic-shape schemas). The check is static
    on the abstract method; we just confirm the parameter
    is part of the documented signature so a future
    provider can't drop it accidentally."""
    import inspect

    from magi.agent.llm.provider import LLMProvider

    sig = inspect.signature(LLMProvider.chat)
    assert "tools" in sig.parameters


def test_tool_registry_returns_expected_schemas(tmp_path, monkeypatch):
    """Stable list of v0 tool names. ``list`` order
    matters — the LLM sees tools in this order every
    turn, so a reorder would be a perceptible UI
    change. ``MAGI_STATE_DIR`` is set so the registry
    can build role-gate tools (which lazily open a
    session)."""
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(tmp_path))
    names = [t["name"] for t in get_tool_schemas()]
    assert names == [
        "read_file",
        "write_file",
        "edit_file",
        "list_files",
        "search_sessions",
        "send_message",
        "schedule_task",
        "load_skill",
        # Shell execution — three tools the LLM uses
        # together to run + monitor + kill background
        # shell processes.
        "bash",
        "bash_output",
        "bash_kill",
        # MAGI memory management — the LLM calls these
        # when the operator says "记住 X" / "完成了".
        "add_memory",
        "update_memory",
        "complete_memory",
        "delete_memory",
        # Contact directory — the LLM calls these
        # when the operator says "记住 Lily 在财务部".
        "add_contact",
        "update_contact",
        "delete_contact",
        "search_contacts",
        # Todo / action-item — per-employee (admin /
        # assigned only). Registry filters them out of
        # the menu for other roles; tests see the full
        # list when ``caller_role`` defaults to ``None``.
        "add_todo",
        "complete_todo",
        "list_todo",
    ]


def test_get_tool_lookup_hits_and_misses(tmp_path, monkeypatch):
    """Registry lookup. ``MAGI_STATE_DIR`` is set so the
    registry can build role-gate tools (which lazily
    open a session)."""
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(tmp_path))
    assert get_tool("read_file") is not None
    assert get_tool("does_not_exist") is None


# ────────────────────────────────────────────────────────────────── #
# safe_resolve
# ────────────────────────────────────────────────────────────────── #


def test_safe_resolve_rejects_path_traversal(tmp_path):
    """``../etc/passwd`` is rejected even though the
    string itself doesn't start with ``/``."""
    with pytest.raises(ValueError, match="escapes workspace"):
        safe_resolve(tmp_path, "../etc/passwd")


def test_safe_resolve_rejects_absolute_paths(tmp_path):
    """Absolute paths are rejected because they bypass
    the workspace-relative semantics."""
    with pytest.raises(ValueError, match="escapes workspace"):
        safe_resolve(tmp_path, "/etc/passwd")


def test_safe_resolve_rejects_long_paths(tmp_path):
    """Path length cap is a defensive guard against the
    LLM emitting an unbounded string."""
    with pytest.raises(ValueError, match="path too long"):
        safe_resolve(tmp_path, "a" * 2000)


def test_safe_resolve_rejects_nonexistent(tmp_path):
    """``must_be_file=True`` (the default) raises when the
    file doesn't exist."""
    with pytest.raises(ValueError, match="does not exist"):
        safe_resolve(tmp_path, "nope.txt")


def test_safe_resolve_rejects_directory_when_must_be_file(tmp_path):
    """Asking for ``must_be_file`` on a directory is
    different from ``list_files`` semantics."""
    (tmp_path / "sub").mkdir()
    with pytest.raises(ValueError, match="directory"):
        safe_resolve(tmp_path, "sub")


def test_safe_resolve_allows_dirs_when_must_be_file_false(tmp_path):
    """``write_file`` calls with ``must_be_file=False`` so
    it can create a new file in an empty directory."""
    (tmp_path / "sub").mkdir()
    target = safe_resolve(tmp_path, "sub/new.txt", must_be_file=False)
    assert target == (tmp_path / "sub" / "new.txt").resolve()


# ────────────────────────────────────────────────────────────────── #
# Tools — end-to-end on a real tmp workspace
# ────────────────────────────────────────────────────────────────── #


@pytest.fixture
def workspace_ctx(tmp_path, monkeypatch):
    """A ``ToolContext`` pointing at a fresh tmp workspace."""
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(tmp_path))
    # Reset the SQLAlchemy engine singleton so each test
    # gets a fresh engine pointing at this test's
    # tmp_path. Without this, the second test onwards
    # writes to the first test's DB and the third test's
    # rows appear in the fourth test's results.
    import magi.agent.db.engine as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None
    return ToolContext(
        state_dir=str(tmp_path / "state"),
        workspace=tmp_path,
        chat_id="9001",
        employee_id=42,
        channel="webui",
    )


@pytest.mark.asyncio
async def test_read_file_returns_content(workspace_ctx):
    (workspace_ctx.workspace / "SOUL.md").write_text(
        "# soul\n\nhello\n", encoding="utf-8"
    )
    result = await ReadFileTool().run(workspace_ctx, path="SOUL.md")
    assert result.is_error is False
    assert "# soul" in result.content
    assert "hello" in result.content


@pytest.mark.asyncio
async def test_read_file_rejects_traversal(workspace_ctx):
    """The LLM trying ``../../etc/passwd`` gets a clear
    error, not a leaked /etc/passwd read."""
    result = await ReadFileTool().run(workspace_ctx, path="../etc/passwd")
    assert result.is_error is True
    assert "escapes workspace" in result.content


@pytest.mark.asyncio
async def test_read_file_truncates_large_files(workspace_ctx):
    """A file > 8 KB is truncated with a notice so the
    LLM knows there's more."""
    big = "x" * (10 * 1024)
    (workspace_ctx.workspace / "big.txt").write_text(big, encoding="utf-8")
    result = await ReadFileTool().run(workspace_ctx, path="big.txt")
    assert result.is_error is False
    assert "truncated at 8192 bytes" in result.content
    # The actual returned text is shorter than the original
    # by more than the 2 KB of "head dropped" — exact
    # boundary depends on UTF-8 char alignment.
    assert len(result.content) < len(big)


@pytest.mark.asyncio
async def test_write_file_creates_file_and_parent_dirs(workspace_ctx):
    result = await WriteFileTool().run(
        workspace_ctx, path="notes/today.md", content="hi",
    )
    assert result.is_error is False
    target = workspace_ctx.workspace / "notes" / "today.md"
    assert target.read_text(encoding="utf-8") == "hi"


@pytest.mark.asyncio
async def test_write_file_overwrites_atomically(workspace_ctx):
    """A successful ``write_file`` replaces the prior
    content; a no-leftover-tempfile invariant is harder
    to assert without hooking tempfile internals, but the
    file's final contents prove the atomic rename
    happened."""
    target = workspace_ctx.workspace / "f.txt"
    target.write_text("old", encoding="utf-8")
    await WriteFileTool().run(workspace_ctx, path="f.txt", content="new")
    assert target.read_text(encoding="utf-8") == "new"
    assert list(workspace_ctx.workspace.glob(".f.txt.*.tmp")) == []


@pytest.mark.asyncio
async def test_write_file_rejects_traversal(workspace_ctx):
    result = await WriteFileTool().run(
        workspace_ctx, path="../escape.txt", content="x",
    )
    assert result.is_error is True
    assert "escapes workspace" in result.content
    # Verify nothing leaked to disk outside the workspace.
    assert not (workspace_ctx.workspace.parent / "escape.txt").exists()


@pytest.mark.asyncio
async def test_write_file_rejects_oversized_content(workspace_ctx):
    """The 256 KB cap is a guard against runaway LLM
    output."""
    result = await WriteFileTool().run(
        workspace_ctx, path="big.txt", content="x" * (300 * 1024),
    )
    assert result.is_error is True
    assert "limit" in result.content


@pytest.mark.asyncio
async def test_list_files_returns_entries(workspace_ctx):
    (workspace_ctx.workspace / "a.txt").write_text("a")
    (workspace_ctx.workspace / "b").mkdir()
    result = await ListFilesTool().run(workspace_ctx, path=".")
    assert result.is_error is False
    body = json.loads(result.content)
    names = {e["name"] for e in body["entries"]}
    assert names == {"a.txt", "b"}
    # The directory entry should be marked as ``dir``.
    dir_entry = next(e for e in body["entries"] if e["name"] == "b")
    assert dir_entry["type"] == "dir"


@pytest.mark.asyncio
async def test_list_files_rejects_file_path(workspace_ctx):
    """Passing a file path (not a directory) is an
    error — ``list_files`` only enumerates directories."""
    (workspace_ctx.workspace / "a.txt").write_text("a")
    result = await ListFilesTool().run(workspace_ctx, path="a.txt")
    assert result.is_error is True
    assert "not a directory" in result.content


@pytest.mark.asyncio
async def test_send_message_webui_returns_error(workspace_ctx):
    """``send_message`` is TG-only in v0. On webui the
    LLM gets a clear error so it stops trying."""
    result = await SendMessageTool().run(workspace_ctx, text="hi")
    assert result.is_error is True
    assert "webui" in result.content or "not available" in result.content


@pytest.mark.asyncio
async def test_send_message_tg_calls_callback(workspace_ctx):
    """When the channel is ``tg`` and a callback is
    provided, the tool invokes it with (chat_id, text)."""
    callback = AsyncMock()
    ctx = ToolContext(
        state_dir=workspace_ctx.state_dir,
        workspace=workspace_ctx.workspace,
        chat_id="9001",
        employee_id=42,
        channel="tg",
    )
    result = await SendMessageTool().run(
        ctx, text="hi there", _tg_send_callback=callback,
    )
    assert result.is_error is False
    callback.assert_awaited_once_with(9001, "hi there")


@pytest.mark.asyncio
async def test_send_message_tg_no_callback_returns_error():
    """A TG channel call without an injected callback
    returns an error rather than silently dropping the
    message — that's a programming error, not a runtime
    condition, and the operator should see the loud
    failure."""
    ctx = ToolContext(
        state_dir="/tmp/x", workspace=Path("/tmp/x"),
        chat_id="9001", employee_id=42, channel="tg",
    )
    result = await SendMessageTool().run(ctx, text="hi")
    assert result.is_error is True


@pytest.mark.asyncio
async def test_send_message_rejects_oversized_text(workspace_ctx):
    """4000-char cap matches the TG API limit."""
    result = await SendMessageTool().run(
        workspace_ctx, text="x" * 5000,
    )
    assert result.is_error is True
    assert "limit" in result.content


# (End-to-end agent-loop tests removed — see module
# docstring for the rationale. The 24 unit tests above
# cover the tools + schema + safety surface; the loop
# itself is exercised by the live smoke in
# ``tests/manual/test_tools_live.py``.)


# ───────────────────────────────────────────────────────────────── #
# read_file — windowed mode (offset / limit)
# ───────────────────────────────────────────────────────────────── #


@pytest.mark.asyncio
async def test_read_file_windowed_returns_line_numbers(workspace_ctx):
    """With ``offset`` the tool returns ``"     N|<line>"``
    output (1-indexed, right-aligned). The reference
    bash tool uses the same shape so the LLM can
    cross-reference line numbers across tools.
    """
    (workspace_ctx.workspace / "script.py").write_text(
        "alpha\nbravo\ncharlie\ndelta\n",
        encoding="utf-8",
    )
    result = await ReadFileTool().run(
        workspace_ctx, path="script.py", offset=2, limit=2,
    )
    assert result.is_error is False
    # Lines 2-3 (1-indexed) of the file.
    assert "     2|bravo" in result.content
    assert "     3|charlie" in result.content
    # Line 1 and 4 are NOT in the window.
    assert "alpha" not in result.content
    assert "delta" not in result.content
    # Header announces the window.
    assert "[lines 2-3 of 4" in result.content
    # Suffix offers the next page.
    assert "offset=4" in result.content


@pytest.mark.asyncio
async def test_read_file_windowed_offset_past_end_errors(workspace_ctx):
    """``offset`` past EOF is a clean error, not a
    silent empty result."""
    (workspace_ctx.workspace / "short.txt").write_text(
        "one\ntwo\n", encoding="utf-8",
    )
    result = await ReadFileTool().run(
        workspace_ctx, path="short.txt", offset=100,
    )
    assert result.is_error is True
    assert "past the end" in result.content


@pytest.mark.asyncio
async def test_read_file_windowed_no_more_pages_omits_suffix(workspace_ctx):
    """When the window reaches the end of the file,
    the "more lines" suffix is omitted (the LLM
    doesn't get a useless prompt to keep paging).
    """
    (workspace_ctx.workspace / "tiny.txt").write_text(
        "one\ntwo\n", encoding="utf-8",
    )
    result = await ReadFileTool().run(
        workspace_ctx, path="tiny.txt", offset=1, limit=10,
    )
    assert result.is_error is False
    # All 2 lines returned, no continuation hint.
    assert "one" in result.content
    assert "two" in result.content
    assert "more lines" not in result.content


@pytest.mark.asyncio
async def test_read_full_file_still_no_line_numbers(workspace_ctx):
    """Without ``offset``/``limit`` we keep the
    v0 format: raw text, byte-truncated at 8 KB.
    The line-numbered output is opt-in via offset —
    callers that just want the file don't get
    noisy prefixes.
    """
    (workspace_ctx.workspace / "raw.txt").write_text(
        "first\nsecond\n", encoding="utf-8",
    )
    result = await ReadFileTool().run(workspace_ctx, path="raw.txt")
    assert result.is_error is False
    # Plain text — no ``N|`` prefix.
    assert "first\nsecond" in result.content
    assert "|" not in result.content.split("\n")[0]


# ───────────────────────────────────────────────────────────────── #
# edit_file
# ───────────────────────────────────────────────────────────────── #


@pytest.mark.asyncio
async def test_edit_file_replaces_unique_match(workspace_ctx):
    """Happy path: the LLM sends a unique ``old_str``
    and a ``new_str``; the file is updated and
    the original content is preserved everywhere
    else."""
    from magi.agent.tools.edit_file import EditFileTool
    target = workspace_ctx.workspace / "config.yaml"
    target.write_text(
        "name: app\nversion: 1\nport: 8080\n",
        encoding="utf-8",
    )
    tool = EditFileTool()
    result = await tool.run(
        workspace_ctx,
        path="config.yaml",
        old_str="port: 8080",
        new_str="port: 9090",
    )
    assert result.is_error is False
    assert "replaced" in result.content.lower()
    # Disk reflects the change; the rest of the
    # file is untouched.
    assert target.read_text(encoding="utf-8") == (
        "name: app\nversion: 1\nport: 9090\n"
    )


@pytest.mark.asyncio
async def test_edit_file_rejects_non_unique_match(workspace_ctx):
    """If ``old_str`` appears more than once the
    tool fails with a clear message — silently
    patching the first occurrence is the kind of
    footgun the LLM needs a guard against.
    """
    from magi.agent.tools.edit_file import EditFileTool
    target = workspace_ctx.workspace / "dupe.txt"
    target.write_text("foo\nbar\nfoo\n", encoding="utf-8")
    tool = EditFileTool()
    result = await tool.run(
        workspace_ctx,
        path="dupe.txt",
        old_str="foo",
        new_str="baz",
    )
    assert result.is_error is True
    assert "2 times" in result.content
    # File unchanged.
    assert target.read_text(encoding="utf-8") == "foo\nbar\nfoo\n"


@pytest.mark.asyncio
async def test_edit_file_rejects_missing_match(workspace_ctx):
    """``old_str`` not in the file → clear error,
    not a silent append / replacement of nothing.
    """
    from magi.agent.tools.edit_file import EditFileTool
    target = workspace_ctx.workspace / "f.txt"
    target.write_text("hello\n", encoding="utf-8")
    tool = EditFileTool()
    result = await tool.run(
        workspace_ctx,
        path="f.txt",
        old_str="goodbye",
        new_str="anything",
    )
    assert result.is_error is True
    assert "not found" in result.content


@pytest.mark.asyncio
async def test_edit_file_supports_empty_new_str(workspace_ctx):
    """``new_str=""`` deletes the matched chunk.
    Common LLM pattern: drop a debug print.
    """
    from magi.agent.tools.edit_file import EditFileTool
    target = workspace_ctx.workspace / "code.py"
    target.write_text(
        "def f():\n    print('debug')\n    return 1\n",
        encoding="utf-8",
    )
    tool = EditFileTool()
    result = await tool.run(
        workspace_ctx,
        path="code.py",
        old_str="    print('debug')\n",
        new_str="",
    )
    assert result.is_error is False
    assert target.read_text(encoding="utf-8") == (
        "def f():\n    return 1\n"
    )


@pytest.mark.asyncio
async def test_edit_file_rejects_traversal(workspace_ctx):
    """Path resolution goes through ``safe_resolve`` —
    a ``../etc/passwd`` attempt is rejected, same
    as read/write.
    """
    from magi.agent.tools.edit_file import EditFileTool
    tool = EditFileTool()
    result = await tool.run(
        workspace_ctx,
        path="../etc/passwd",
        old_str="root",
        new_str="nobody",
    )
    assert result.is_error is True
    assert "escapes workspace" in result.content


@pytest.mark.asyncio
async def test_edit_file_rejects_non_utf8(workspace_ctx):
    """Editing a non-UTF-8 file fails with a clear
    message rather than corrupting bytes."""
    from magi.agent.tools.edit_file import EditFileTool
    target = workspace_ctx.workspace / "binary.bin"
    target.write_bytes(b"\x80\x81\x82not-utf-8")
    tool = EditFileTool()
    result = await tool.run(
        workspace_ctx,
        path="binary.bin",
        old_str="not-utf-8",
        new_str="anything",
    )
    assert result.is_error is True
    assert "not valid UTF-8" in result.content


@pytest.mark.asyncio
async def test_edit_file_rejects_oversized_old_str(workspace_ctx):
    """A 100 KB ``old_str`` is almost always the LLM
    pasting the whole file. Cap it at 64 KB with a
    helpful message.
    """
    from magi.agent.tools.edit_file import EditFileTool
    target = workspace_ctx.workspace / "big.txt"
    target.write_text("hello\n", encoding="utf-8")
    tool = EditFileTool()
    result = await tool.run(
        workspace_ctx,
        path="big.txt",
        old_str="x" * (70 * 1024),
        new_str="y",
    )
    assert result.is_error is True
    assert "smaller chunk" in result.content


@pytest.mark.asyncio
async def test_edit_file_atomicity_preserves_previous_on_failure(
    workspace_ctx, monkeypatch,
):
    """A failed write must NOT leave the file
    half-written. We simulate a failure by
    monkey-patching ``os.replace`` to raise and
    verify the original content is still on disk.
    """
    from magi.agent.tools import edit_file
    from magi.agent.tools.edit_file import EditFileTool

    target = workspace_ctx.workspace / "atomic.txt"
    original = "line1\nline2\nline3\n"
    target.write_text(original, encoding="utf-8")

    real_replace = edit_file.os.replace
    def boom(src, dst):
        raise OSError("simulated disk full")
    monkeypatch.setattr(edit_file.os, "replace", boom)
    try:
        tool = EditFileTool()
        result = await tool.run(
            workspace_ctx,
            path="atomic.txt",
            old_str="line2",
            new_str="NEW",
        )
    finally:
        monkeypatch.setattr(edit_file.os, "replace", real_replace)

    assert result.is_error is True
    # Original is intact — no half-written tmp file
    # leaked, no truncate happened.
    assert target.read_text(encoding="utf-8") == original
    # No leftover .tmp file (the cleanup path ran).
    leftovers = [
        p.name for p in target.parent.iterdir()
        if p.name.startswith(".atomic.txt.")
    ]
    assert leftovers == [], f"leftover tmp files: {leftovers}"


def test_edit_file_appears_in_registry(tmp_path, monkeypatch):
    """Sanity: edit_file is registered alongside the
    other file tools.
    """
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(tmp_path))
    from magi.agent.tools.registry import get_tool_schemas
    names = [t["name"] for t in get_tool_schemas()]
    assert "edit_file" in names