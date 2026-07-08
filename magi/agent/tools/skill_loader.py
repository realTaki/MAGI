"""SKILL.md loader — scans two roots for skills on demand.

A skill is a directory under either root, containing a
``SKILL.md`` file with YAML frontmatter:

    ---
    name: web_lookup                # required, must match the dir name
    description: 互联网检索 ......   # required, used in the system block
    version: "1.0"                  # optional
    ---

    # markdown body follows

Two roots are scanned, in this order:
  - ``magi/skills/`` — the **bundle** shipped with the
    image. Always available; lives next to the package
    source. Acts as the default catalog the deployer
    can customise away.
  - ``<workspace>/skills/`` — the **operator** directory.
    Derived from ``MAGI_WORKSPACE_DIR`` (or the default
    ``<state_dir>/..``). Operator-edited SKILL.md files
    here override bundle entries with the same name
    without warning — that is the normal "I want to
    customise this skill" flow.

  ---

The loader reads the frontmatter (description only — the body

    ---
containing a ``SKILL.md`` file with YAML frontmatter:

    ---
    name: web_lookup                # required, must match the dir name
    description: 互联网检索 ......   # required, used in the system block
    version: "1.0"                  # optional
    ---

    # markdown body follows

The loader reads the frontmatter (description only — the body
is **not** returned by the loader, only by the `load_skill`
tool the LLM can call). Body retrieval is intentional: a 2K
system-prompt block that fits the LLM's context budget
without competing with the actual conversation. Skills with
fat bodies (operator manuals, full runbooks) don't blow up
the per-turn input.

Failure modes (graceful, never crash boot):

  - duplicate ``name`` across dirs → last-write wins, log a warning
  - malformed frontmatter → skip the skill, log a warning
  - missing ``name`` key → use the directory basename, fall through
  - empty/missing ``skills/`` dir → empty registry, log INFO

YAML parsing uses PyYAML if installed; otherwise we fall
back to a tiny line-prefix parser that handles the common
``key: value`` shape (one key per line, no nested blocks).
PyYAML is preferred — the fallback only handles our own
templates and won't cover quirks. We pin PyYAML in
``pyproject.toml`` so production deploys have it for sure.
"""

from __future__ import annotations
from magi.agent.db.engine import require_state_dir

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("magi.agent.skills.loader")


_SKILL_SUBDIR_NAME = "skills"
_SKILL_FILENAME = "SKILL.md"

# Path to the bundle shipped with the image. ``skill_loader.py``
# lives at ``magi/agent/tools/skill_loader.py``; the bundle sits
# at ``magi/skills/`` — one level up from the package, one
# level further up from the package's parent. Resolved at
# import time so the path is stable across ``MAGI_WORKSPACE_DIR``
# overrides.
_BUNDLE_SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"
# 1-2 sentence description is the sweet spot — fits the
# system-prompt block without bloating, tells the LLM
# when to reach for ``load_skill``.
_DESCRIPTION_MAX = 240
_NAME_RE = re.compile(r"^[a-zA-Z0-9_.\-]{1,64}$")


@dataclass(frozen=True)
class SkillMeta:
    """One row in the registry.

    The body is **not** stored here. ``format_skills_block``
    only needs ``name`` and ``description``. The body is
    read on demand by :class:`magi.agent.skills.loader_tool.SkillLoaderTool`.
    """

    name: str
    description: str
    path: Path
    version: Optional[str] = None


def _workspace_root() -> Path:
    """Mirror :func:`magi.agent.workspace.workspace_root` — we
    inline the implementation to avoid a circular import path
    at module load time (agent → skills loader → workspace).

    Both ``MAGI_WORKSPACE_DIR`` override and the default
    (``<state_dir>/..`` — typically ``/workspace``) are
    honoured.
    """
    override = os.environ.get("MAGI_WORKSPACE_DIR")
    if override:
        return Path(override)
    state_dir = require_state_dir()
    return Path(state_dir).parent


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    """Extract YAML frontmatter at the file's head + return the body.

    Frontmatter is delimited by ``---`` lines. We try PyYAML
    first; on import failure we fall back to a tiny
    ``key: value`` parser that handles our own flat
    templates. Either way the body starts **after** the
    closing ``---``.
    """
    if not raw.startswith("---"):
        return {}, raw
    lines = raw.splitlines()
    # Find the closing ``---`` line (line index >= 1).
    close_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            close_idx = i
            break
    if close_idx == -1:
        return {}, raw  # malformed frontmatter → treat as raw
    fm_lines = lines[1:close_idx]
    body = "\n".join(lines[close_idx + 1 :])
    fm: dict[str, str] = {}
    # PyYAML first.
    try:
        import yaml  # PyYAML

        # ``safe_load`` refuses arbitrary tags; we only want
        # ``str`` / ``int`` / ``bool`` / ``float`` here.
        parsed = yaml.safe_load("\n".join(fm_lines)) or {}
        if isinstance(parsed, dict):
            fm = {str(k): ("" if v is None else str(v)) for k, v in parsed.items()}
            return fm, body
    except ImportError:
        pass
    # Fallback: ``key: value`` per line, value is the rest of
    # the line stripped. ``v: 'foo bar'`` (quoted strings) is
    # handled by stripping the quotes. Nested values aren't
    # handled — we don't use any.
    for raw_line in fm_lines:
        line = raw_line.rstrip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in ("'", '"')
        ):
            value = value[1:-1]
        fm[key.strip()] = value
    return fm, body


def _skill_name_from_dir(skill_dir: Path) -> Optional[str]:
    """Validate the directory name is a usable skill name.

    Returns ``None`` for invalid names so callers can
    log + skip without translating the regex themselves.
    """
    name = skill_dir.name
    if not _NAME_RE.match(name):
        logger.warning(
            "skills: %s has invalid name %r, skipping", skill_dir, name,
        )
        return None
    return name


def _truncate_description(text: str) -> str:
    """Single-line, max DESCRIPTION_MAX chars, '…' suffix."""
    text = " ".join(text.split())
    if len(text) > _DESCRIPTION_MAX:
        text = text[: _DESCRIPTION_MAX - 1] + "…"
    return text


class SkillLoader:
    """One-pass scanner of the bundle root + the operator root.

    Two passes during ``__init__``:

      1. Bundle first (``magi/skills/``) — populates the
         registry with the defaults shipped in the image.
      2. Operator second (``<workspace>/skills/``) — any
         same-named skill here overwrites the bundle
         entry. No warning: "I edited web_lookup to fit
         my domain" is the normal flow, not a collision.

    The class is **stateful** in that the registry is built
    once at construction; access through
    :func:`get_skill_loader` returns the same instance for
    the lifetime of the process.

    Hot-reload is intentionally not implemented in v0:
    operators add a SKILL.md, restart the MAGI node, the
    new skill appears. Adding a watcher (inotify on
    Linux) is plausible but the project memory's
    "minimal by default" rule keeps it out until a real
    use case shows up.
    """

    def __init__(
        self,
        workspace_root: Path,
        bundle_dir: Path | None = None,
    ) -> None:
        """Build the registry.

        ``workspace_root`` is the operator root
        (``<workspace>/skills/``). ``bundle_dir`` is the
        image-shipped default catalog (``magi/skills/``);
        defaults to the package's own ``_BUNDLE_SKILLS_DIR``
        constant. Pass ``bundle_dir=Path("/nonexistent")``
        (or any non-existent path) to skip the bundle
        entirely — useful for tests that only want the
        operator half.
        """
        self._workspace_root = Path(workspace_root)
        self._skills_dir = self._workspace_root / _SKILL_SUBDIR_NAME
        self._bundle_dir = (
            Path(bundle_dir) if bundle_dir is not None else _BUNDLE_SKILLS_DIR
        )
        self._registry: dict[str, SkillMeta] = {}
        self._load()

    # ─── public surface ────────────────────────────────────────────────

    def list(self) -> list[SkillMeta]:
        """Sorted by skill name for stable UI ordering."""
        return sorted(self._registry.values(), key=lambda s: s.name)

    def get(self, name: str) -> Optional[SkillMeta]:
        return self._registry.get(name)

    # ─── internal ─────────────────────────────────────────────────────

    def _load(self) -> None:
        """Idempotent: clearing the registry first means
        calling :meth:`__init__` twice on the same dir
        behaves the same as once (used by tests).

        Two passes — bundle first (the defaults), then
        operator (overrides bundle entries with the same
        name; no warning, that is the normal customisation
        flow). Each root is logged separately so the
        operator sees which path contributed what.
        """
        self._registry.clear()
        bundle_count = self._load_root(self._bundle_dir, source="bundle")
        operator_count_before = len(self._registry)
        self._load_root(self._skills_dir, source="operator")
        operator_count = len(self._registry) - operator_count_before
        logger.info(
            "skills: %d loaded (%d from bundle, %d from operator)",
            len(self._registry), bundle_count, operator_count,
        )

    def _load_root(self, root: Path, *, source: str) -> int:
        """Scan one root, register its skills, return count.

        Missing or non-directory root → 0 skills, logged at
        INFO/WARN by severity. Operator with no
        ``skills/`` subdir yet is the common case on a
        fresh deploy — INFO, not WARN, so the boot log
        stays clean.
        """
        if not root.exists():
            level = logger.info if source == "operator" else logger.warning
            level(
                "skills: %s root %s does not exist; no skills from %s",
                source, root, source,
            )
            return 0
        if not root.is_dir():
            logger.warning(
                "skills: %s root %s is not a directory; skipping",
                source, root,
            )
            return 0
        before = len(self._registry)
        for skill_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            self._load_one(skill_dir, source=source)
        loaded = len(self._registry) - before
        logger.info(
            "skills: %d loaded from %s root %s",
            loaded, source, root,
        )
        return loaded

    def _load_one(self, skill_dir: Path, *, source: str = "operator") -> None:
        skill_path = skill_dir / _SKILL_FILENAME
        if not skill_path.is_file():
            logger.debug(
                "skills: %s has no SKILL.md; skipping", skill_dir,
            )
            return
        try:
            raw = skill_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "skills: failed to read %s: %s; skipping",
                skill_path, exc,
            )
            return
        fm, _body = _parse_frontmatter(raw)
        # Name resolution priority: explicit frontmatter
        # ``name`` → directory basename. We DON'T enforce
        # that the two match (someday a deployer will want
        # an alias); just warn on mismatch.
        declared_name = fm.get("name", "").strip()
        dir_name = _skill_name_from_dir(skill_dir)
        if dir_name is None:
            return  # already logged
        if declared_name and declared_name != dir_name:
            logger.warning(
                "skills: %s declares name=%r but dir is %r; "
                "using the directory name",
                skill_path, declared_name, dir_name,
            )
        name = dir_name
        description_raw = fm.get("description", "").strip()
        if not description_raw:
            # An empty description wastes the system-prompt
            # slot — skip the skill rather than register
            # it with a placeholder.
            logger.warning(
                "skills: %s has no description; skipping", skill_path,
            )
            return
        description = _truncate_description(description_raw)
        version = fm.get("version", "").strip() or None
        # Duplicate-name handling:
        #   - operator over bundle → silent. That is the
        #     normal "I edited web_lookup to my domain" flow;
        #     warning every boot would be noise.
        #   - bundle over operator (shouldn't happen given
        #     the load order, but defensive) → warning.
        #   - same-source duplicates → warning so the
        #     deployer sees the conflict.
        if name in self._registry:
            existing = self._registry[name]
            if source == "operator" and existing.path.is_relative_to(_BUNDLE_SKILLS_DIR):
                logger.debug(
                    "skills: operator %s overrides bundle %s for name %r",
                    skill_path, existing.path, name,
                )
            else:
                logger.warning(
                    "skills: duplicate name %r — overwriting previous "
                    "definition at %s with %s",
                    name, existing.path, skill_path,
                )
        self._registry[name] = SkillMeta(
            name=name,
            description=description,
            path=skill_path,
            version=version,
        )


# ──────────────────────────────────────────────────────────────────────── #
# Module-level singleton
# ──────────────────────────────────────────────────────────────────────── #


_skill_loader: Optional[SkillLoader] = None
_skill_loader_lock = __import__("threading").RLock()


def get_skill_loader() -> SkillLoader:
    """Build (or return) the module singleton.

    Honours ``MAGI_WORKSPACE_DIR`` (or the default derived
    from ``MAGI_STATE_DIR``) for the root path — same
    rule the rest of the runtime uses, so a deployer
    pointing ``MAGI_WORKSPACE_DIR`` at a different tree
    has their skills land in the right place.
    """
    global _skill_loader
    with _skill_loader_lock:
        if _skill_loader is None:
            _skill_loader = SkillLoader(
                _workspace_root(), bundle_dir=_BUNDLE_SKILLS_DIR,
            )
        return _skill_loader


def _reset_for_tests() -> None:
    """Test-only: drop the singleton. Production never calls this."""
    global _skill_loader
    _skill_loader = None


# ──────────────────────────────────────────────────────────────────────── #
# System-prompt formatter
# ──────────────────────────────────────────────────────────────────────── #


def format_skills_block(skills: list[SkillMeta]) -> str:
    """Render an "Available skills" block for the system prompt.

    Returns an empty string when there are no skills — agent
    loop short-circuits and uses ``soul`` verbatim, saving
    every turn a few tokens.

    The block is **Markdown-ish** — bullets, plain
    ``name — description`` style, easily parsed by every
    LLM we currently ship to (Anthropic and OpenAI both
    handle the format without problem).
    """
    if not skills:
        return ""
    lines = ["", "## Available skills", ""]
    lines.append(
        "下面是本 MAGI 节点上注册的 skill 列表。每个 skill 的 "
        "**完整正文**仅在你需要细节时通过 `load_skill(name)` "
        "tool 拉取 — 这里只展示摘要。挑出最相关那个 skill 之后，"
        "用 `load_skill(\"<name>\")` 取正文参考。"
    )
    lines.append("")
    for s in skills:
        # One bullet per skill. Version is appended in
        # parentheses when present so a draft/bump cycle
        # is observable in the system-prompt audit log.
        if s.version:
            lines.append(f"- **{s.name}** (v{s.version}) — {s.description}")
        else:
            lines.append(f"- **{s.name}** — {s.description}")
    return "\n".join(lines)


__all__ = [
    "SkillMeta",
    "SkillLoader",
    "get_skill_loader",
    "format_skills_block",
    "_reset_for_tests",
]
