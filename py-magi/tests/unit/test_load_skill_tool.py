"""Unit tests for :class:`tools.skills.load_skill.LoadSkillTool`.

The tool is a thin wrapper around ``ctx.bus.skills_book``. We
construct a real :class:`SkillsBook` against tmp_path roots and
feed it into the tool via a stub :class:`ToolContext` so we
exercise the full dispatch path without spinning up a bus
bootstrap.

Like the Book tests, the book fixture is a factory — the Book
scans at construction, so skills must be written first.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from old_bus.bases.db.file import FileShelf
from old_bus.firmwares.books.file.skillsBook import SkillsBook
from tools.BaseTool import ToolContext
from tools.skills.load_skill import LoadSkillTool

# ──────────────────────────────────────────────────────────────────────── #
# Fixtures + helpers
# ──────────────────────────────────────────────────────────────────────── #


def _make_skill(root: Path, name: str, *, description: str, body: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return skill_dir / "SKILL.md"


@pytest.fixture
def two_roots(tmp_path: Path) -> tuple[Path, Path]:
    bundle = tmp_path / "bundle"
    operator = tmp_path / "operator"
    bundle.mkdir()
    operator.mkdir()
    return bundle, operator


@pytest.fixture
def build_ctx(two_roots: tuple[Path, Path]):
    """Factory that builds a stub :class:`ToolContext` carrying a
    freshly-scanned :class:`SkillsBook`. Tests write skills first,
    then call this."""

    def _build() -> ToolContext:
        bundle_root, operator_root = two_roots
        book = SkillsBook(FileShelf(bundle_root), FileShelf(operator_root))
        # Stub ``bus`` with just the one attribute the tool reads.
        # A real ``Bus`` requires 29+ ORM-backed fields; the tool
        # only ever touches ``bus.skills_book`` so we don't need any
        # of them.
        fake_bus = SimpleNamespace(skills_book=book)
        return ToolContext(workspace="", contact_id=1, channel="test", bus=fake_bus)

    return _build


@pytest.fixture
def tool() -> LoadSkillTool:
    return LoadSkillTool()


# ──────────────────────────────────────────────────────────────────────── #
# Happy path
# ──────────────────────────────────────────────────────────────────────── #


async def test_load_skill_returns_body_content(
    tool: LoadSkillTool,
    build_ctx,
    two_roots,
):
    bundle_root, _ = two_roots
    _make_skill(
        bundle_root,
        "alpha",
        description="alpha skill",
        body="alpha body line\n",
    )
    ctx = build_ctx()
    result = await tool.run(ctx, name="alpha")
    assert result.is_error is False
    assert "alpha body line" in result.content
    # Frontmatter stripped.
    assert "name: alpha" not in result.content


async def test_load_skill_includes_root_dir_line(
    tool: LoadSkillTool,
    build_ctx,
    two_roots,
):
    bundle_root, _ = two_roots
    skill_dir = bundle_root / "rooty"
    _make_skill(skill_dir.parent, "rooty", description="r", body="x")
    ctx = build_ctx()
    result = await tool.run(ctx, name="rooty")
    assert "**Skill Root Directory:**" in result.content
    assert str(skill_dir) in result.content


# ──────────────────────────────────────────────────────────────────────── #
# Error paths
# ──────────────────────────────────────────────────────────────────────── #


async def test_load_skill_unknown_name_returns_friendly_message(
    tool: LoadSkillTool,
    build_ctx,
):
    """A missing skill is *not* an error — the LLM might guess and
    we want it to pivot gracefully (``is_error=False``)."""
    ctx = build_ctx()
    result = await tool.run(ctx, name="ghost")
    assert result.is_error is False
    assert "no skill named 'ghost' is registered" in result.content


async def test_load_skill_empty_name_is_error(
    tool: LoadSkillTool,
    build_ctx,
):
    ctx = build_ctx()
    result = await tool.run(ctx, name="")
    assert result.is_error is True
    assert "required" in result.content.lower()


async def test_load_skill_path_traversal_is_error(
    tool: LoadSkillTool,
    build_ctx,
):
    """The name regex blocks ``..`` and slashes — a malicious
    LLM-emitted name is rejected at the regex, not at a later
    file IO."""
    ctx = build_ctx()
    for bad in ("../etc/passwd", "foo/bar", "with space", "weird!char"):
        result = await tool.run(ctx, name=bad)
        assert result.is_error is True, f"expected error for name={bad!r}"
        assert "invalid skill name" in result.content


async def test_load_skill_no_bus_returns_require_bus_error():
    """The ``@BaseTool.require_bus`` decorator fails closed when
    ``ctx.bus is None`` — see :class:`tools.BaseTool.BaseTool`'s
    decorator."""
    tool = LoadSkillTool()
    ctx = ToolContext(workspace="", contact_id=1, channel="test", bus=None)
    result = await tool.run(ctx, name="anything")
    assert result.is_error is True
    assert "tool context has no bus" in result.content


# ──────────────────────────────────────────────────────────────────────── #
# Role gating
# ──────────────────────────────────────────────────────────────────────── #


def test_load_skill_is_gated_to_admin_and_assigned():
    """The class-level ``ALLOWED_ROLES`` should match the other
    admin-tier tools (``ScheduleTaskTool`` / action items)."""
    assert LoadSkillTool.ALLOWED_ROLES == frozenset({"admin", "assigned"})
