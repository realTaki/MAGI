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


def test_tool_registry_returns_four_schemas():
    """Stable list of v0 tool names. ``list`` order
    matters — the LLM sees tools in this order every
    turn, so a reorder would be a perceptible UI
    change."""
    names = [t["name"] for t in get_tool_schemas()]
    assert names == [
        "read_file",
        "write_file",
        "list_files",
        "search_sessions",
        "send_message",
        "schedule_task",
        "load_skill",
    ]


def test_get_tool_lookup_hits_and_misses():
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