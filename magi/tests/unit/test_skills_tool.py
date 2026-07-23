"""Tests for the ``load_skill`` tool surface."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from magi.agent.tools.skill_loader import _reset_for_tests
from magi.agent.tools.skill_loader_tool import SkillLoaderTool
from magi.agent.tools.base import ToolContext, ToolResult


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / "skills").mkdir(parents=True)
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(ws))
    _reset_for_tests()
    yield ws
    _reset_for_tests()


def _ctx(workspace_root: Path) -> ToolContext:
    # ``ctx.workspace`` is only inspected for ``safe_resolve``
    # in some tools; our skill loader uses ``MAGI_WORKSPACE_DIR``
    # directly via the singleton, so we don't care that this
    # is a stub value.
    return ToolContext(
        state_dir="ignored",
        workspace=workspace_root,
        
        uid=0,
        channel="webui",
    )


def _write_skill(
    workspace_root: Path,
    name: str,
    body: str = "正文",
    description: str | None = None,
):
    skill_dir = workspace_root / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    desc = description if description is not None else f"{name} skill"
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n\n{body}\n",
        encoding="utf-8",
    )


def test_load_skill_returns_body(workspace):
    _write_skill(workspace, "alpha", "alpha body — should be returned")
    tool = SkillLoaderTool()
    result = asyncio_run(tool.run(_ctx(workspace), name="alpha"))
    assert isinstance(result, ToolResult)
    assert "alpha body — should be returned" in result.content
    assert result.is_error is False


def test_load_skill_unknown_name_returns_friendly_message(workspace):
    """A missing skill is NOT an error — the LLM should
    see a normal message so it can pivot (e.g. read the
    file directly via ``read_file``)."""
    _write_skill(workspace, "real")
    tool = SkillLoaderTool()
    result = asyncio_run(tool.run(_ctx(workspace), name="does-not-exist"))
    assert result.is_error is False
    assert "does-not-exist" in result.content


def test_load_skill_blank_name_is_error(workspace):
    tool = SkillLoaderTool()
    result = asyncio_run(tool.run(_ctx(workspace), name=""))
    assert result.is_error is True


def test_load_skill_rejects_unsafe_name(workspace):
    """Path-traversal is rejected — we don't want the LLM
    reading arbitrary files through this tool."""
    tool = SkillLoaderTool()
    result = asyncio_run(tool.run(_ctx(workspace), name="../../etc/passwd"))
    assert result.is_error is True


def test_load_skill_truncates_oversized_body(workspace):
    """Bodies larger than the cap (32 KB) are truncated
    with a marker so the LLM knows there's unread content."""
    big = "x" * (40 * 1024)  # 40 KB
    _write_skill(workspace, "huge", big)
    tool = SkillLoaderTool()
    result = asyncio_run(tool.run(_ctx(workspace), name="huge"))
    assert result.is_error is False
    assert len(result.content) < 40 * 1024
    assert "truncated" in result.content


def test_load_skill_uses_magisolated_singleton(workspace):
    """The tool reads the SkillLoader singleton at
    construction. After a singleton reset, the new
    instance points at the freshly-scanned state."""
    _write_skill(workspace, "alpha")
    tool_a = SkillLoaderTool()
    res_a = asyncio_run(tool_a.run(_ctx(workspace), name="alpha"))
    assert "alpha" in res_a.content


# Minimal async runner for sync test execution (no pytest-asyncio
# ceremony; tests stay readable as documentation).
import asyncio


def asyncio_run(coro):
    """Drive an async coroutine to completion. Each test
    needs its own loop to avoid ``'RuntimeError: Event
    loop is closed'`` between cases."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
