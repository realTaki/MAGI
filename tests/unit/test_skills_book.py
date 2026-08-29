"""Unit tests for ``magi.bus.firmwares.books.file.skillsBook.SkillsBook``.

Two tmp_path-based roots per test (bundle + operator) so each test
gets an isolated, predictable filesystem layout.  The Book scans
both roots at construction; tests therefore write skills FIRST, then
construct the Book — not the other way around (otherwise the scan
would race ahead of the writes).

The default factory (``build_default_skills_book``) is exercised by
the ``test_default_factory_*`` tests; everything else constructs a
:class:`SkillsBook` directly so it can control both roots.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from magi.old_bus.bases.db.file import FileShelf
from magi.old_bus.firmwares.books.file.skillsBook import (
    _BODY_MAX_BYTES,
    SkillBody,
    SkillMeta,
    SkillNotFound,
    SkillsBook,
    build_default_skills_book,
)

# ──────────────────────────────────────────────────────────────────────── #
# Fixtures
# ──────────────────────────────────────────────────────────────────────── #


def _write_skill(
    root: Path,
    name: str,
    *,
    description: str = "test description",
    version: str | None = None,
    extra_frontmatter: str = "",
    body: str = "Hello skill body.\n",
) -> Path:
    """Create ``<root>/<name>/SKILL.md`` with the given frontmatter + body."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    fm_lines = [
        "---",
        f"name: {name}",
        f"description: {description}",
    ]
    if version:
        fm_lines.append(f'version: "{version}"')
    if extra_frontmatter:
        fm_lines.append(extra_frontmatter)
    fm_lines.append("---")
    content = "\n".join(fm_lines) + "\n\n" + body
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir / "SKILL.md"


@pytest.fixture
def two_roots(tmp_path: Path) -> tuple[Path, Path]:
    """Return (bundle_root, operator_root) as sibling tmp dirs."""
    bundle = tmp_path / "bundle"
    operator = tmp_path / "operator"
    bundle.mkdir()
    operator.mkdir()
    return bundle, operator


@pytest.fixture
def build_book(two_roots: tuple[Path, Path]):
    """Factory that builds a fresh :class:`SkillsBook` over the two
    test roots. Tests write skills first, then call this to scan.

    The Book scans at construction, so building it before the writes
    would race ahead and find an empty registry.
    """
    bundle_root, operator_root = two_roots

    def _build() -> SkillsBook:
        return SkillsBook(
            FileShelf(bundle_root),
            FileShelf(operator_root),
        )

    return _build


# ──────────────────────────────────────────────────────────────────────── #
# Scan semantics
# ──────────────────────────────────────────────────────────────────────── #


def test_scan_finds_bundle_and_operator(two_roots, build_book):
    bundle_root, operator_root = two_roots
    _write_skill(bundle_root, "alpha", description="the alpha skill")
    _write_skill(operator_root, "beta", description="the beta skill")
    book = build_book()

    metas = {m.name for m in book.list()}
    assert metas == {"alpha", "beta"}


def test_scan_returns_sorted_by_name(two_roots, build_book):
    bundle_root, _ = two_roots
    for name in ("zeta", "alpha", "mu"):
        _write_skill(bundle_root, name, description=f"{name} skill")
    book = build_book()

    names = [m.name for m in book.list()]
    assert names == ["alpha", "mu", "zeta"]


def test_operator_overrides_bundle_silently(two_roots, build_book):
    """Same-name skills: operator wins, no warning needed."""
    bundle_root, operator_root = two_roots
    _write_skill(
        bundle_root,
        "shared",
        description="from bundle",
        version="1.0",
    )
    _write_skill(
        operator_root,
        "shared",
        description="from operator (overridden)",
        version="2.0",
    )
    book = build_book()

    meta = book.get("shared")
    assert meta is not None
    assert meta.description == "from operator (overridden)"
    assert meta.version == "2.0"
    # Path points into the operator root
    assert meta.path.parent.parent == operator_root


def test_unknown_name_returns_none(build_book):
    book = build_book()
    assert book.get("does_not_exist") is None
    assert book.exists("does_not_exist") is False


def test_malformed_frontmatter_is_skipped(two_roots, build_book):
    """A SKILL.md without a closing ``---`` is treated as body-only
    and skipped — no description means nothing to put in the system
    prompt, so it can't be registered."""
    bundle_root, _ = two_roots
    skill_dir = bundle_root / "broken"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: broken\nnever closes\n\nbody\n",
        encoding="utf-8",
    )
    book = build_book()
    assert book.get("broken") is None


def test_missing_description_is_skipped(two_roots, build_book):
    bundle_root, _ = two_roots
    skill_dir = bundle_root / "nodesc"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: nodesc\n---\n\nbody\n",
        encoding="utf-8",
    )
    book = build_book()
    assert book.get("nodesc") is None


def test_invalid_dir_name_is_skipped(two_roots, build_book):
    """The regex only accepts ``[a-zA-Z0-9_.-]{1,64}`` — names with
    spaces, slashes, or punctuation outside that alphabet are
    skipped at scan time."""
    bundle_root, _ = two_roots
    for bad in ("with space", "weird!char", "中文"):
        skill_dir = bundle_root / bad
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: x\ndescription: x\n---\n\nbody\n",
            encoding="utf-8",
        )
    book = build_book()
    assert book.list() == []


def test_dir_without_skill_md_is_skipped(two_roots, build_book):
    bundle_root, _ = two_roots
    (bundle_root / "no_md_here").mkdir()
    (bundle_root / "no_md_here" / "other.txt").write_text("not a skill")
    book = build_book()
    assert book.list() == []


def test_typed_frontmatter_fields_are_coerced(two_roots, build_book):
    """YAML-typed ``allowed-tools`` / ``metadata`` end up on the
    :class:`SkillMeta` (stashed for future features; not yet acted
    on)."""
    bundle_root, _ = two_roots
    _write_skill(
        bundle_root,
        "rich",
        description="a skill with optional fields",
        extra_frontmatter=(
            'license: "Apache-2.0"\n'
            "allowed-tools:\n"
            "  - bash\n"
            "  - read_file\n"
            "metadata:\n"
            "  domain: ops\n"
            "  owner: alice\n"
        ),
    )
    book = build_book()
    meta = book.get("rich")
    assert isinstance(meta, SkillMeta)
    assert meta.license == "Apache-2.0"
    assert meta.allowed_tools == ["bash", "read_file"]
    assert meta.metadata == {"domain": "ops", "owner": "alice"}


def test_description_is_truncated(two_roots, build_book):
    bundle_root, _ = two_roots
    long_desc = "x " * 500  # 1000 chars
    _write_skill(bundle_root, "long", description=long_desc)
    book = build_book()
    meta = book.get("long")
    assert meta is not None
    # ``_DESCRIPTION_MAX = 240``; one truncation char suffix.
    assert len(meta.description) == 240
    assert meta.description.endswith("…")


# ──────────────────────────────────────────────────────────────────────── #
# read_body
# ──────────────────────────────────────────────────────────────────────── #


def test_read_body_strips_frontmatter(two_roots, build_book):
    bundle_root, _ = two_roots
    _write_skill(
        bundle_root,
        "strip",
        description="strip me",
        body="this is the body",
    )
    book = build_book()
    body = book.read_body("strip")
    assert "name:" not in body.content
    assert "description:" not in body.content
    assert "this is the body" in body.content


def test_read_body_prepends_root_dir_line(two_roots, build_book):
    bundle_root, _ = two_roots
    skill_dir = bundle_root / "rooty"
    skill_dir.mkdir()
    _write_skill(bundle_root, "rooty", description="root me", body="x")
    book = build_book()
    body = book.read_body("rooty")
    assert "**Skill Root Directory:**" in body.content
    # The path in the hint is the skill's parent dir.
    assert str(skill_dir) in body.content


def test_read_body_returns_skillbody_with_mtime_and_truncated_false(
    two_roots,
    build_book,
):
    bundle_root, _ = two_roots
    _write_skill(bundle_root, "small", description="small", body="hi\n")
    book = build_book()
    body = book.read_body("small")
    assert isinstance(body, SkillBody)
    assert body.truncated is False
    # mtime is UTC-aware datetime.
    assert isinstance(body.mtime, datetime)
    assert body.mtime.tzinfo == UTC


def test_read_body_unknown_name_raises(build_book):
    book = build_book()
    with pytest.raises(SkillNotFound):
        book.read_body("ghost")


def test_read_body_truncates_utf8_safely(two_roots, build_book):
    """A CJK string that would be split mid-rune if we cut at the
    byte boundary must NOT be split — the rewind loop walks back
    over UTF-8 continuation bytes before decoding.

    The :data:`_BODY_MAX_BYTES` cap applies to the **body text**
    (post-frontmatter-strip, post-rewrite, pre-prepend). The full
    ``content`` string includes the root-dir hint (prepended) and
    the truncation marker (appended), so it may exceed the cap
    by a few dozen bytes.
    """
    bundle_root, _ = two_roots
    skill_dir = bundle_root / "big"
    skill_dir.mkdir()
    # Construct a body whose total size > _BODY_MAX_BYTES and whose
    # rune at the boundary is a 3-byte CJK character.
    prefix = "a" * (_BODY_MAX_BYTES - 1)
    # 3-byte CJK char × 5 — total 15 bytes straddling the cut.
    suffix = "中" * 5
    body_text = prefix + suffix
    # Write a SKILL.md that wraps ``body_text`` in a body block.
    (skill_dir / "SKILL.md").write_text(
        "---\nname: big\ndescription: big\n---\n\n" + body_text + "\n",
        encoding="utf-8",
    )
    book = build_book()

    body = book.read_body("big")
    assert body.truncated is True
    # The body must decode cleanly to UTF-8 — the rewind walks back
    # over continuation bytes (``10xxxxxx``), so the last code point
    # is always complete. Re-encoding via ``errors="strict"`` proves
    # no rune was split mid-cut.
    raw = body.content.encode("utf-8", errors="strict")
    # Body that was past the truncation point is gone — the
    # trailing CJK characters that straddled the cut are not all
    # present (some were dropped by the rewind, all by the cap).
    # The "中" × 5 suffix is 15 bytes; with the prefix being one
    # byte short of the cap, only 1 byte of the first "中" would
    # be inside the cut — the rewind walks back, so none of the
    # trailing runes survive.
    assert not raw.endswith("中中中中中".encode())
    # Truncation marker is appended.
    assert b"truncated at 32768 bytes" in raw


def test_read_body_rewrites_existing_scripts_path(two_roots, build_book):
    """``scripts/foo.py`` in the body becomes an absolute path when
    the file exists on disk. (Pattern 1 — bare-directory paths — is
    rewritten without a hint; Pattern 2 — prose references like
    "see scripts/foo.py" — adds the read_file hint.)
    """
    bundle_root, _ = two_roots
    skill_dir = bundle_root / "rewrite"
    skill_dir.mkdir()
    scripts = skill_dir / "scripts"
    scripts.mkdir()
    (scripts / "foo.py").write_text("# hello\n", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(
        "---\nname: rewrite\ndescription: rewrite me\n---\n\nRun scripts/foo.py for the example.\n",
        encoding="utf-8",
    )
    book = build_book()

    body = book.read_body("rewrite")
    # Absolute path of the sibling script appears in the rewritten body.
    assert str(scripts / "foo.py") in body.content


def test_read_body_adds_read_file_hint_on_prose_references(
    two_roots,
    build_book,
):
    """Pattern 2 — prose references like ``see foo.md`` — adds the
    ``(use read_file to access)`` hint so the LLM knows how to fetch
    the referenced file."""
    bundle_root, _ = two_roots
    skill_dir = bundle_root / "prose"
    skill_dir.mkdir()
    (skill_dir / "reference.md").write_text(
        "# ref\n",
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(
        "---\nname: prose\ndescription: prose refs\n---\n\nsee reference.md for details.\n",
        encoding="utf-8",
    )
    book = build_book()

    body = book.read_body("prose")
    assert str(skill_dir / "reference.md") in body.content
    assert "use read_file to access" in body.content


def test_read_body_leaves_nonexistent_paths_alone(two_roots, build_book):
    """A `scripts/foo.py` reference that doesn't exist on disk is
    left untouched — no hallucinated absolute paths."""
    bundle_root, _ = two_roots
    skill_dir = bundle_root / "noexist"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: noexist\ndescription: nope\n---\n\nSee scripts/never_written.py for details.\n",
        encoding="utf-8",
    )
    book = build_book()

    body = book.read_body("noexist")
    assert "scripts/never_written.py" in body.content
    # No absolute-path injection.
    assert str(skill_dir / "scripts" / "never_written.py") not in body.content


def test_read_body_marks_truncation(two_roots, build_book):
    bundle_root, _ = two_roots
    skill_dir = bundle_root / "huge"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: huge\ndescription: huge\n---\n\n" + ("x" * (_BODY_MAX_BYTES + 100)) + "\n",
        encoding="utf-8",
    )
    book = build_book()
    body = book.read_body("huge")
    assert body.truncated is True
    assert "truncated" in body.content


# ──────────────────────────────────────────────────────────────────────── #
# Hot-reload (on-disk fingerprint)
# ──────────────────────────────────────────────────────────────────────── #


def test_hot_reload_picks_up_newly_added_skill_dir(two_roots, build_book):
    """Adding a brand-new skill directory after construction is
    visible on the next ``list()`` / ``get()`` / ``exists()`` call —
    no restart."""
    bundle_root, _ = two_roots
    book = build_book()
    assert book.get("late") is None

    _write_skill(bundle_root, "late", description="latecomer")
    assert {m.name for m in book.list()} == {"late"}
    meta = book.get("late")
    assert meta is not None
    assert meta.description == "latecomer"
    assert book.exists("late") is True


def test_hot_reload_picks_up_removed_skill_dir(two_roots, build_book):
    """Removing a skill directory (or its SKILL.md) makes the skill
    disappear from the registry on the next read."""
    bundle_root, _ = two_roots
    _write_skill(bundle_root, "doomed", description="will be removed")
    book = build_book()
    assert book.exists("doomed") is True

    import shutil

    shutil.rmtree(bundle_root / "doomed")

    assert book.exists("doomed") is False
    assert book.get("doomed") is None
    assert {m.name for m in book.list()} == set()


def test_hot_reload_picks_up_edited_skill_md(two_roots, build_book):
    """Editing ``SKILL.md`` (description change) is reflected on the
    next ``get()``."""
    bundle_root, _ = two_roots
    _write_skill(bundle_root, "editable", description="v1")
    book = build_book()
    assert book.get("editable").description == "v1"

    # Rewrite the SKILL.md with a new description. ``write_text`` is
    # atomic via tmp+rename on most platforms and definitely bumps
    # mtime — fingerprint catches it.
    import time

    time.sleep(0.01)  # ensure mtime delta on coarse-resolution FS
    skill_path = bundle_root / "editable" / "SKILL.md"
    skill_path.write_text(
        "---\nname: editable\ndescription: v2\n---\n\nnew body\n",
        encoding="utf-8",
    )

    assert book.get("editable").description == "v2"
    # ``read_body`` reads fresh from disk; new body is visible.
    body = book.read_body("editable")
    assert "new body" in body.content


def test_hot_reload_picks_up_operator_override_of_bundle(two_roots, build_book):
    """Adding an operator-side skill with the same name as a bundle
    skill silently overrides the bundle entry — no restart."""
    bundle_root, operator_root = two_roots
    _write_skill(bundle_root, "shared", description="from bundle")
    book = build_book()
    assert book.get("shared").description == "from bundle"

    _write_skill(operator_root, "shared", description="from operator")
    assert book.get("shared").description == "from operator"


def test_hot_reload_is_idempotent_when_nothing_changed(
    two_roots,
    build_book,
):
    """A read that triggers no change is a no-op — same registry
    object is returned (no churn). Implementation detail: the fast
    path skips the re-scan."""
    bundle_root, _ = two_roots
    _write_skill(bundle_root, "stable", description="unchanged")
    book = build_book()
    meta_before = book.get("stable")
    meta_after = book.get("stable")
    assert meta_before is meta_after  # dataclass frozen → same instance


def test_hot_reload_handles_skills_subdir_added_late(two_roots, build_book):
    """Operator root created AFTER the book is built (the common
    fresh-deploy case where ensure_workspace ran but skills/ was
    empty) is picked up when files land later."""
    _, operator_root = two_roots
    book = build_book()
    assert {m.name for m in book.list()} == set()

    _write_skill(operator_root, "first", description="operator's first")
    assert {m.name for m in book.list()} == {"first"}


# ──────────────────────────────────────────────────────────────────────── #
# Default factory
# ──────────────────────────────────────────────────────────────────────── #


def test_default_factory_resolves_bundle(tmp_path: Path):
    """``build_default_skills_book`` anchors the bundle on
    ``magi.__file__`` parent — verify it points at the shipped
    skills (``<magi>/skills/``) and includes the three defaults.

    The workspace is created with a ``skills/`` subdir up front
    (matches what ``ensure_workspace`` does in production), so the
    factory's ``create_root=False`` is sufficient.
    """
    workspace = tmp_path / "ws"
    (workspace / "skills").mkdir(parents=True)

    book = build_default_skills_book(workspace)
    names = {m.name for m in book.list()}
    # The three image-shipped defaults. If the bundle ships a
    # different set, this test will tell us.
    assert {"codebase_search", "reminder_template", "web_lookup"} <= names
