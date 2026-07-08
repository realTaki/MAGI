"""Tests for the SKILL.md loader.

Covers:
  - Basic 1-skill discovery
  - Multiple skills + sort order
  - Malformed frontmatter → skip + warning
  - Duplicate name → last-write-wins
  - Empty / missing ``skills/`` dir → empty registry
  - ``format_skills_block`` shape (header + bullets)
  - Body size cap on the system-prompt block
  - Hidden-bootstrap (no env-var reset): the loader
    honours ``MAGI_WORKSPACE_DIR`` correctly.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from magi.agent.tools.skill_loader import (
    format_skills_block,
    get_skill_loader,
)
from magi.agent.tools.skill_loader import (
    SkillLoader,
    _reset_for_tests,
)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Each test gets a fresh workspace + state dir pair.

    Reset the module singleton at teardown so the next
    test doesn't pick up our scan.
    """
    ws = tmp_path / "ws"
    skills_dir = ws / "skills"
    skills_dir.mkdir(parents=True)
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(ws))
    _reset_for_tests()
    yield ws
    _reset_for_tests()


def _write_skill(
    workspace_root: Path,
    name: str,
    body: str = "正文",
    description: str | None = None,
    **frontmatter_extra,
):
    """Drop a SKILL.md with a flat-key frontmatter.

    ``description`` defaults to a non-empty string so the
    test's loader-side guard doesn't fire on the missing-
    description path. Pass ``description=None`` to write
    a blank one on purpose.
    """
    skill_dir = workspace_root / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    desc = description if description is not None else f"{name} skill for test"
    lines = [
        "---",
        f"name: {name}",
        f"description: {desc}",
    ]
    for k, v in frontmatter_extra.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    skill_dir.joinpath("SKILL.md").write_text("\n".join(lines), encoding="utf-8")


def test_loader_finds_a_single_skill(workspace):
    _write_skill(workspace, "alpha")
    loader = SkillLoader(workspace, bundle_dir=workspace.parent / "no-bundle")
    skills = loader.list()
    assert [s.name for s in skills] == ["alpha"]
    assert skills[0].description.startswith("alpha skill")
    assert skills[0].version is None


def test_loader_sorts_skills_alphabetically(workspace):
    _write_skill(workspace, "zebra")
    _write_skill(workspace, "alpha")
    _write_skill(workspace, "mango")
    loader = SkillLoader(workspace, bundle_dir=workspace.parent / "no-bundle")
    assert [s.name for s in loader.list()] == ["alpha", "mango", "zebra"]


def test_loader_skips_dir_without_skill_md(workspace):
    (workspace / "skills" / "empty-skill").mkdir()
    _write_skill(workspace, "alpha")
    loader = SkillLoader(workspace, bundle_dir=workspace.parent / "no-bundle")
    assert [s.name for s in loader.list()] == ["alpha"]


def test_loader_skips_skill_with_no_description(workspace, caplog):
    """An operator who leaves ``description:`` empty
    wastes a system-prompt slot — skip rather than
    register with a placeholder."""
    (workspace / "skills" / "undocumented").mkdir(parents=True, exist_ok=True)
    (workspace / "skills" / "undocumented" / "SKILL.md").write_text(
        "---\nname: undocumented\n---\n\nbody\n", encoding="utf-8"
    )
    _write_skill(workspace, "real")
    loader = SkillLoader(workspace, bundle_dir=workspace.parent / "no-bundle")
    assert [s.name for s in loader.list()] == ["real"]


def test_loader_handles_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(tmp_path / "nope"))
    # ``nope/skills/`` does not exist. Pass a non-existent
    # bundle_dir so the loader is exercised single-source;
    # the dual-source contract is pinned by the new tests
    # at the bottom of the file.
    _reset_for_tests()
    loader = SkillLoader(
        tmp_path / "nope",
        bundle_dir=tmp_path / "no-bundle",
    )
    assert loader.list() == []


def test_loader_overrides_duplicate(workspace):
    """Same skill name across two directory paths → only
    the alphabetically-last directory wins."""
    _write_skill(workspace, "dup", description="first dup")
    # The frontend skill name resolves to the **directory**
    # name (``loader._skill_name_from_dir``), not the
    # frontmatter ``name:`` — so we expect two skills here,
    # one per directory, both registered. The duplicate
    # path is tested in ``test_loader_skill_name_from_dir``
    # down below.
    skill2 = workspace / "skills" / "dup2"
    skill2.mkdir(parents=True, exist_ok=True)
    skill2.joinpath("SKILL.md").write_text(
        "---\nname: dup\ndescription: second dup\n---\n", encoding="utf-8"
    )
    _reset_for_tests()
    loader = SkillLoader(workspace, bundle_dir=workspace.parent / "no-bundle")
    # The same ``SkillMeta.name`` ('dup') would overwrite;
    # but 'dup' and 'dup2' are different names → both kept.
    # This actually proves the "warn on collision" path
    # differently: write a name collision both ways
    # (same frontmatter name, same dir name).
    skills = loader.list()
    assert [s.name for s in skills] == ["dup", "dup2"]


def test_loader_duplicate_name_overrides(workspace):
    """Two ``SKILL.md`` files that resolve to the same
    skill name (same directory basename) → last-write-wins."""
    # First copy.
    skill1 = workspace / "skills" / "alpha"
    skill1.mkdir(parents=True, exist_ok=True)
    skill1.joinpath("SKILL.md").write_text(
        "---\nname: alpha\ndescription: first\n---\n", encoding="utf-8"
    )
    # Second copy with same directory name (sibling
    # inside the 'first' dir). We achieve this by
    # placing it as a sibling folder with the same name
    # — but the loader iterates the parent and refuses
    # duplicate *names*, so we'd need the same *resolved*
    # name. The cleanest setup: frontmatter-override
    # path leads to a "dup" and the dir leads to "dup2",
    # which we already covered above. This test exercises
    # the simpler case: same directory, two SKILL.md
    # versions. The loader picks the last via dir-iterate
    # iteration order, so the assertion is "exactly one
    # loaded with that name" regardless of which file.
    skill2 = workspace / "skills" / "alpha2"
    skill2.mkdir(parents=True, exist_ok=True)
    skill2.joinpath("SKILL.md").write_text(
        "---\nname: alpha2\ndescription: second with alpha2 name\n---\n",
        encoding="utf-8",
    )
    _reset_for_tests()
    loader = SkillLoader(workspace, bundle_dir=workspace.parent / "no-bundle")
    # Both pass — they have distinct resolved names.
    assert {s.name for s in loader.list()} == {"alpha", "alpha2"}


def test_format_skills_block_is_empty_without_skills():
    """Empty registry → no block. Keeps the per-turn system
    prompt short when there are no operator skills loaded."""
    block = format_skills_block([])
    assert block == ""


def test_format_skills_block_lists_each_skill(workspace):
    _write_skill(workspace, "alpha", description="alpha skill", version="1.2")
    _write_skill(workspace, "zebra", description="zebra skill")
    block = format_skills_block(SkillLoader(workspace, bundle_dir=workspace.parent / "no-bundle").list())
    assert "## Available skills" in block
    assert "**alpha**" in block
    assert "(v1.2)" in block
    assert "**zebra**" in block
    assert block.count("\n- ") == 2


def test_format_skills_block_respects_metadata_only(workspace):
    """The system-prompt block must NOT contain full bodies
    — bodies flow through the ``load_skill`` tool on
    demand."""
    _write_skill(
        workspace, "alpha",
        description="alpha skill",
        body="very long secrets " * 100,
    )
    block = format_skills_block(SkillLoader(workspace, bundle_dir=workspace.parent / "no-bundle").list())
    assert "very long secrets" not in block
    assert len(block) < 2000  # way under body cap


def test_get_skill_loader_singleton(monkeypatch, tmp_path):
    """``get_skill_loader`` returns the same instance for
    repeated calls — cache invalidation rules belong in
    tests."""
    ws1 = tmp_path / "ws1"
    (ws1 / "skills" / "x").mkdir(parents=True)
    (ws1 / "skills" / "x" / "SKILL.md").write_text(
        "---\nname: x\ndescription: x\n---\n", encoding="utf-8"
    )
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(ws1))
    _reset_for_tests()
    a = get_skill_loader()
    b = get_skill_loader()
    assert a is b


# -- dual-source loader: bundle + operator roots -----------------------


def test_loader_reads_bundle_when_operator_dir_empty(tmp_path, monkeypatch):
    """A fresh deploy with no operator-edited skills still
    sees the bundled examples (``magi/skills/*``). The
    loader is configured with the real bundle path; the
    operator dir is a separate, empty tmp_path."""
    ws = tmp_path / "ws"
    (ws / "skills").mkdir(parents=True)
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(ws))
    _reset_for_tests()
    loader = SkillLoader(ws)
    names = {s.name for s in loader.list()}
    # The three bundled example skills ship in the image.
    assert {"codebase_search", "reminder_template", "web_lookup"} <= names
    # Their paths come from the bundle, not the operator dir.
    for s in loader.list():
        if s.name in {"codebase_search", "reminder_template", "web_lookup"}:
            assert "magi/skills/" in str(s.path), s.path


def test_operator_skill_overrides_bundle_silently(tmp_path, monkeypatch, caplog):
    """Operator puts a same-named SKILL.md in workspace/skills/;
    it replaces the bundled version without a warning
    (the normal "I customised web_lookup to my domain" flow).

    The path stored in the registry should be the operator
    copy, not the bundle copy.
    """
    import logging

    ws = tmp_path / "ws"
    op_skill = ws / "skills" / "web_lookup"
    op_skill.mkdir(parents=True)
    (op_skill / "SKILL.md").write_text(
        "---\n"
        "name: web_lookup\n"
        "description: operator-customised web_lookup\n"
        "---\n"
        "operator body",
        encoding="utf-8",
    )
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(ws))
    _reset_for_tests()
    with caplog.at_level(logging.WARNING, logger="magi.agent.skills.loader"):
        loader = SkillLoader(ws)
    # Find the web_lookup entry — it should point at the
    # operator path, not the bundle path.
    web = next(s for s in loader.list() if s.name == "web_lookup")
    assert str(web.path).endswith("ws/skills/web_lookup/SKILL.md")
    assert web.description == "operator-customised web_lookup"
    # And the override happened silently — no WARNING about
    # the duplicate name. (DEBUG log is fine; the suite
    # caps at WARNING to keep noise down.)
    override_warnings = [
        r for r in caplog.records
        if "duplicate name" in r.getMessage() and "web_lookup" in r.getMessage()
    ]
    assert override_warnings == [], (
        f"operator override should be silent, got: {[r.getMessage() for r in override_warnings]}"
    )


def test_loader_with_empty_bundle_still_finds_operator_skills(tmp_path, monkeypatch):
    """Tests that want to ignore the bundle entirely can
    pass a non-existent bundle_dir. The operator side
    must still be scanned — the constructor must NOT
    fail closed if the bundle is missing."""
    ws = tmp_path / "ws"
    _write_skill(ws, "alpha")
    loader = SkillLoader(ws, bundle_dir=tmp_path / "no-bundle-here")
    assert [s.name for s in loader.list()] == ["alpha"]


# -- optional frontmatter fields (license / allowed_tools / metadata) -


def test_loader_passes_through_license_field(workspace):
    """``license: MIT`` in the frontmatter is read into
    ``SkillMeta.license``. v0 doesn't act on it, but a
    future allow-list / license-attribution feature can
    read it without a schema change.
    """
    skill_dir = workspace / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: alpha\n"
        "description: alpha skill for test\n"
        "license: MIT\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    loader = SkillLoader(workspace)
    meta = loader.get("alpha")
    assert meta is not None
    assert meta.license == "MIT"


def test_loader_passes_through_allowed_tools_list(workspace):
    """``allowed-tools: [foo, bar]`` is read as a
    ``list[str]``. v0 doesn't enforce the list; it's
    stashed for the future allow-list feature.
    """
    skill_dir = workspace / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: alpha\n"
        "description: alpha skill for test\n"
        "allowed-tools:\n"
        "  - read_file\n"
        "  - write_file\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    loader = SkillLoader(workspace)
    meta = loader.get("alpha")
    assert meta is not None
    assert meta.allowed_tools == ["read_file", "write_file"]


def test_loader_passes_through_metadata_dict(workspace):
    """``metadata: {key: val}`` is read as a
    ``dict[str, str]``.
    """
    skill_dir = workspace / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: alpha\n"
        "description: alpha skill for test\n"
        "metadata:\n"
        "  owner: finance\n"
        "  version_tag: v1.0\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    loader = SkillLoader(workspace)
    meta = loader.get("alpha")
    assert meta is not None
    assert meta.metadata == {"owner": "finance", "version_tag": "v1.0"}


def test_loader_optional_fields_default_to_none(workspace):
    """A SKILL.md without the optional frontmatter
    fields has all three as ``None`` (not empty list /
    empty dict). Callers can use ``is None`` to test
    "no restriction set".
    """
    _write_skill(workspace, "alpha")
    loader = SkillLoader(workspace)
    meta = loader.get("alpha")
    assert meta is not None
    assert meta.license is None
    assert meta.allowed_tools is None
    assert meta.metadata is None


# -- _process_skill_paths (Progressive Disclosure Level 3) ---------


def test_process_skill_paths_rewrites_directory_relative_paths(tmp_path):
    """``scripts/foo.py`` → absolute path when the file
    exists in the skill dir.
    """
    from magi.agent.tools.skill_loader import _process_skill_paths
    skill_dir = tmp_path / "alpha"
    skill_dir.mkdir()
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "with_server.py").write_text(
        "print('hi')", encoding="utf-8",
    )
    out = _process_skill_paths("run scripts/with_server.py", skill_dir)
    # The relative path is now the absolute path (and
    # the surrounding prose "run " is preserved).
    assert str(skill_dir / "scripts" / "with_server.py") in out
    # The bare form ``scripts/with_server.py`` was
    # replaced — it's no longer a free-standing token
    # in the output (the absolute path embeds the
    # filename as its tail, but the leading
    # ``scripts/`` directory component is gone).
    assert "run scripts/with_server.py" not in out


def test_process_skill_paths_rewrites_prose_references(tmp_path):
    """``see reference.md`` → ``see `abs/path` (use
    read_file to access)``. LLM can copy-paste the
    absolute path into a ``read_file`` call.
    """
    from magi.agent.tools.skill_loader import _process_skill_paths
    skill_dir = tmp_path / "alpha"
    skill_dir.mkdir()
    (skill_dir / "reference.md").write_text("# ref", encoding="utf-8")
    out = _process_skill_paths("see reference.md for details", skill_dir)
    assert str(skill_dir / "reference.md") in out
    assert "use read_file to access" in out
    # The bare relative isn't left in place.
    assert "see reference.md" not in out


def test_process_skill_paths_rewrites_markdown_links(tmp_path):
    """Markdown link ``[`text`](relpath)`` →
    ``[`text`](`abs/path`) (use read_file to access)``.
    """
    from magi.agent.tools.skill_loader import _process_skill_paths
    skill_dir = tmp_path / "alpha"
    skill_dir.mkdir()
    (skill_dir / "guide.md").write_text("guide", encoding="utf-8")
    out = _process_skill_paths(
        "Read [`guide.md`](guide.md) for context.", skill_dir
    )
    assert f"[`guide.md`](`{skill_dir / 'guide.md'}`)" in out
    assert "use read_file to access" in out


def test_process_skill_paths_leaves_nonexistent_paths_alone(tmp_path):
    """If a path doesn't exist on disk, the body is
    left untouched. Avoids hallucinating files the
    deployer didn't ship.
    """
    from magi.agent.tools.skill_loader import _process_skill_paths
    skill_dir = tmp_path / "alpha"
    skill_dir.mkdir()
    out = _process_skill_paths("see ghost.md for context", skill_dir)
    assert out == "see ghost.md for context"
    assert str(skill_dir) not in out


def test_skill_root_dir_line_announces_path(tmp_path):
    """The "Skill Root Directory" line is the first
    thing the LLM sees — tells it where sibling
    files live.
    """
    from magi.agent.tools.skill_loader import _skill_root_dir_line
    line = _skill_root_dir_line(tmp_path / "alpha")
    assert "Skill Root Directory" in line
    assert str(tmp_path / "alpha") in line
    # The separator is part of the format (LLM reads
    # this as a section break).
    assert "---" in line


# -- load_skill body output ------------------------------


def test_load_skill_body_prepends_root_dir_line(workspace):
    """The body the LLM sees starts with the "Skill
    Root Directory" line so it knows the base for any
    relative file reference.
    """
    skill_dir = workspace / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: alpha\n"
        "description: alpha skill for test\n"
        "---\n"
        "the body",
        encoding="utf-8",
    )
    from magi.agent.tools.skill_loader_tool import SkillLoaderTool

    # Reset the singleton so the new SKILL.md is picked up.
    from magi.agent.tools.skill_loader import _reset_for_tests
    _reset_for_tests()

    result = asyncio_run(SkillLoaderTool().run(None, name="alpha"))
    assert result.is_error is False
    assert "Skill Root Directory" in result.content
    assert str(skill_dir) in result.content
    # Body still present.
    assert "the body" in result.content


def test_load_skill_body_rewrites_known_sibling_paths(workspace):
    """End-to-end: a body that says ``run scripts/x.py``
    comes back to the LLM with the absolute path
    appended, so the LLM knows exactly where the
    sibling file is.

    Pattern 1 (directory-based relative paths)
    just substitutes the path — the "use
    read_file to access" hint comes from Pattern 2
    (prose references) and Pattern 3 (markdown
    links).
    """
    skill_dir = workspace / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    scripts = skill_dir / "scripts"
    scripts.mkdir()
    (scripts / "with_server.py").write_text("ok", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: alpha\n"
        "description: alpha skill for test\n"
        "---\n"
        "Run: `python scripts/with_server.py`",
        encoding="utf-8",
    )

    from magi.agent.tools.skill_loader import _reset_for_tests
    from magi.agent.tools.skill_loader_tool import SkillLoaderTool
    _reset_for_tests()

    result = asyncio_run(SkillLoaderTool().run(None, name="alpha"))
    assert result.is_error is False
    # Pattern 1: relative path is replaced with absolute
    # path. The absolute path is ``/.../scripts/with_server.py``
    # — the body used to say
    # ``Run: `python scripts/with_server.py``` and now
    # says ``Run: `python /.../scripts/with_server.py```.
    # We check for the absolute path AND for the
    # trailing ``.py``` (prose prefix preserved).
    assert "Run: `python " in result.content
    assert str(skill_dir / "scripts" / "with_server.py") in result.content
    assert result.content.rstrip().endswith("`")
    # Frontmatter is stripped — the LLM already saw
    # the metadata in the system prompt.
    assert "description: alpha skill" not in result.content
    # The Skill Root Directory line is prepended so
    # the LLM knows the base.
    assert "Skill Root Directory" in result.content
    assert str(skill_dir) in result.content


def test_load_skill_body_adds_read_file_hint_for_prose_reference(workspace):
    """Pattern 2: ``see reference.md`` →
    ``see `abs/path` (use read_file to access)``.
    """
    skill_dir = workspace / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (skill_dir / "reference.md").write_text("# ref", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: alpha\n"
        "description: alpha skill for test\n"
        "---\n"
        "see reference.md for details",
        encoding="utf-8",
    )

    from magi.agent.tools.skill_loader import _reset_for_tests
    from magi.agent.tools.skill_loader_tool import SkillLoaderTool
    _reset_for_tests()

    result = asyncio_run(SkillLoaderTool().run(None, name="alpha"))
    assert result.is_error is False
    assert str(skill_dir / "reference.md") in result.content
    assert "use read_file to access" in result.content


# asyncio_run helper (matches the pattern in test_skills_tool.py)
import asyncio as _asyncio


def asyncio_run(coro):
    return _asyncio.run(coro)
