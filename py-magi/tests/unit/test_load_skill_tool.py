"""Unit tests for :class:`tools.skills.load_skill.LoadSkillTool`."""

from __future__ import annotations

from pathlib import Path

from bus import Bus
from tools.skills.load_skill import LoadSkillTool


def _write_skill(workspace: Path, name: str, *, description: str, body: str) -> None:
    skill_dir = workspace / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )


async def test_load_skill_returns_body_content(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    with Bus("@load-skill", workspace=workspace) as bus:
        _write_skill(workspace, "alpha", description="alpha skill", body="alpha body line")
        result = await LoadSkillTool(bus=bus).run(name="alpha")
        assert result.is_error is False
        assert "alpha body line" in result.content
        assert "name: alpha" not in result.content


async def test_load_skill_unknown_name_returns_friendly_message(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    with Bus("@load-skill-missing", workspace=workspace) as bus:
        result = await LoadSkillTool(bus=bus).run(name="ghost")
        assert result.is_error is False
        assert "no skill named 'ghost' is registered" in result.content


async def test_load_skill_empty_name_is_error(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    with Bus("@load-skill-empty", workspace=workspace) as bus:
        result = await LoadSkillTool(bus=bus).run(name="")
        assert result.is_error is True
        assert "required" in result.content.lower()


async def test_load_skill_path_traversal_is_error(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    with Bus("@load-skill-bad-name", workspace=workspace) as bus:
        tool = LoadSkillTool(bus=bus)
        for bad in ("../etc/passwd", "foo/bar", "with space", "weird!char"):
            result = await tool.run(name=bad)
            assert result.is_error is True, f"expected error for name={bad!r}"
            assert "invalid skill name" in result.content


async def test_load_skill_no_bus_returns_require_bus_error() -> None:
    result = await LoadSkillTool(bus=None).run(name="anything")
    assert result.is_error is True
    assert "tool was constructed without a bus" in result.content
