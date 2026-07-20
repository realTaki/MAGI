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

    The three optional frontmatter fields (``license`` /
    ``allowed_tools`` / ``metadata``) are read but not
    yet acted on — they're stashed here so a future
    feature (allow-list, audit log, license attribution)
    doesn't need a schema change.
    """

    name: str
    description: str
    path: Path
    version: Optional[str] = None
    license: Optional[str] = None
    # ``allowed-tools`` in the frontmatter is a YAML list
    # (Anthropic skill spec). We store as ``list[str]``;
    # missing / non-list frontmatter values become
    # ``None`` so callers can use ``is None`` as the
    # "no restriction" check.
    allowed_tools: Optional[list[str]] = None
    # ``metadata`` is a free-form ``{key: value}`` map.
    metadata: Optional[dict[str, str]] = None


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


def _parse_frontmatter(raw: str) -> tuple[dict, str, dict]:
    """Extract YAML frontmatter at the file's head + return the body.

    Returns a 3-tuple ``(str_dict, body, typed_dict)``:

    - ``str_dict`` — every value coerced to ``str`` via
      ``str(v)``. The flat ``key: value`` shape v0
      callers (the system-prompt metadata block) read
      from. Stable across PyYAML-present / PyYAML-missing
      paths.
    - ``body`` — the markdown after the closing ``---``.
    - ``typed_dict`` — best-effort PyYAML-typed parse
      (when PyYAML is installed). The new optional
      fields (:class:`SkillMeta.allowed_tools` /
      ``metadata``) read from here. Falls back to an
      empty dict when PyYAML is missing (v0 doesn't
      need them).

    Frontmatter is delimited by ``---`` lines. We try
    PyYAML first; on import failure we fall back to a
    tiny ``key: value`` parser that handles our own
    flat templates. Either way the body starts
    **after** the closing ``---``.
    """
    if not raw.startswith("---"):
        return {}, raw, {}
    lines = raw.splitlines()
    # Find the closing ``---`` line (line index >= 1).
    close_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            close_idx = i
            break
    if close_idx == -1:
        return {}, raw, {}  # malformed frontmatter → treat as raw
    fm_lines = lines[1:close_idx]
    body = "\n".join(lines[close_idx + 1 :])
    fm: dict[str, str] = {}
    typed: dict[str, Any] = {}
    # PyYAML first.
    try:
        import yaml  # PyYAML

        # ``safe_load`` refuses arbitrary tags; we only want
        # ``str`` / ``int`` / ``bool`` / ``float`` here.
        parsed = yaml.safe_load("\n".join(fm_lines)) or {}
        if isinstance(parsed, dict):
            typed = dict(parsed)
            fm = {str(k): ("" if v is None else str(v)) for k, v in parsed.items()}
            return fm, body, typed
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
    return fm, body, typed


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
        fm, _body, typed = _parse_frontmatter(raw)
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
            # Optional frontmatter fields. v0 doesn't act
            # on them; a future allow-list / audit /
            # license-attribution feature can read them
            # without a schema change.
            license=typed.get("license"),
            allowed_tools=_coerce_str_list(typed.get("allowed-tools")),
            metadata=_coerce_str_dict(typed.get("metadata")),
        )


def _coerce_str_list(value: Any) -> Optional[list[str]]:
    """Coerce a frontmatter ``allowed-tools`` value to
    ``list[str]`` or ``None``.

    - ``None`` (key absent) → ``None``
    - YAML list of strings → ``[str, ...]``
    - Anything else (str, int, dict) → ``None``
      silently. The system doesn't crash on weird
      frontmatter; it just ignores the field. A
      warning would be too noisy given how often
      the line is repeated.
    """
    if value is None:
        return None
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return None


def _coerce_str_dict(value: Any) -> Optional[dict[str, str]]:
    """Coerce a frontmatter ``metadata`` value to
    ``dict[str, str]`` or ``None``."""
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items() if k is not None}
    return None


# ──────────────────────────────────────────────────────────────────────── #
# Body path processing — Progressive Disclosure Level 3
# ──────────────────────────────────────────────────────────────────────── #


# File extensions we recognise as "documents the LLM
# might want to read with read_file". The LLM uses this
# to decide "is this a sibling file I should look at"
# vs "is this just prose / a code snippet".
_RECOGNISED_DOC_EXTS = (
    ".md", ".txt", ".json", ".yaml", ".yml",
)


def _process_skill_paths(
    body: str,
    skill_dir: Path,
) -> str:
    """Rewrite relative file references in the skill body
    to absolute paths so the LLM can ``read_file`` them
    directly.

    Three patterns, mirroring the reference skill loader
    (which calls this "Progressive Disclosure Level 3"):

      1. ``scripts/foo.py`` / ``references/bar.md`` —
         plain relative paths. Resolved against
         ``skill_dir`` only if the file exists there.
      2. ``see reference.md`` / ``read forms.md`` —
         prose references to a sibling file. Same
         resolution rule.
      3. ``[`text`](relpath)`` — markdown links.
         ``./`` prefix stripped, then resolved.

    Each rewrite turns the relative reference into
    ``"<abs path> (use read_file to access)"`` so
    the LLM knows how to fetch the file.

    Resolution is **existence-checked** — a path that
    doesn't exist on disk is left alone. This avoids
    hallucinating non-existent files when the skill
    body mentions a file the deployer didn't ship.
    """
    # Pattern 1: directory-based relative paths
    # (``scripts/`` / ``references/`` / ``assets/``). The
    # reference also accepts a leading ``python `` prefix
    # for command-style references. We match the bare
    # directory-style and the ``python <path>`` form.
    def _replace_dir_path(match: "re.Match[str]") -> str:
        prefix = match.group(1) or ""
        rel = match.group(2)
        abs_path = skill_dir / rel
        if abs_path.exists():
            return f"{prefix}{abs_path}"
        return match.group(0)

    # Pattern 1: directory-based relative paths
    # (``scripts/`` / ``references/`` / ``assets/``).
    # Matches bare ``scripts/foo.py`` anywhere in the
    # body, and the ``python <path>`` / `` `<path>``
    # command-style forms. The reference's pattern
    # covers both; the optional ``(python\s+|\s`)``
    # prefix captures the command-style for prefix
    # preservation, and the bare form is captured by
    # the path group alone (no leading prefix chars
    # in the match).
    pattern_dirs = (
        r"(?:(python\s+|\s`))?"          # optional "python " or " `"
        r"((?:scripts|references|assets)/"  # one of the 3 dirs
        r"[^\s`)\]]+)"                    # the rest of the path
    )
    body = re.sub(pattern_dirs, _replace_dir_path, body)

    # Pattern 2: prose references like "see reference.md"
    # / "read forms.md". Suffix is the trailing
    # punctuation / whitespace we want to preserve.
    def _replace_doc_path(match: "re.Match[str]") -> str:
        prefix_word = match.group(1)
        filename = match.group(2)
        suffix = match.group(3) or ""
        abs_path = skill_dir / filename
        if abs_path.exists():
            return (
                f"{prefix_word}`{abs_path}` "
                f"(use read_file to access){suffix}"
            )
        return match.group(0)

    pattern_docs = (
        r"\b(see|read|refer to|check)\s+"
        r"([a-zA-Z0-9_.\-]+\.(?:md|txt|json|yaml|yml))"
        r"([.,;:\s])"
    )
    body = re.sub(pattern_docs, _replace_doc_path, body, flags=re.IGNORECASE)

    # Pattern 3: markdown links — ``[`text`](relpath)``,
    # ``[text](relpath)``, with optional ``./`` prefix.
    # Cap the path segment at 200 chars to avoid
    # runaway backtracking on weird content.
    def _replace_md_link(match: "re.Match[str]") -> str:
        link_text = match.group(1)
        rel = match.group(2)
        # Strip leading ``./`` for the resolve.
        clean = rel[2:] if rel.startswith("./") else rel
        abs_path = skill_dir / clean
        if abs_path.exists():
            return f"[{link_text}](`{abs_path}`) (use read_file to access)"
        return match.group(0)

    pattern_md = (
        r"\[([^]\n]{1,80})\]\("
        r"((?:\./)?[^)\n]{1,200})"
        r"\)"
    )
    body = re.sub(pattern_md, _replace_md_link, body)

    return body


def _skill_root_dir_line(skill_dir: Path) -> str:
    """The first line of the body the LLM sees when it
    ``load_skill``s a skill. Tells the LLM where the
    sibling files live so it can compose absolute
    paths itself if it needs to.

    Kept as a separate helper so the wording can be
    tweaked without touching the path-rewriting logic.
    """
    return (
        f"**Skill Root Directory:** `{skill_dir}`\n\n"
        f"All files and references in this skill are "
        f"relative to this directory.\n\n---\n\n"
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
    # Static header + intro come from the bundled
    # ``skills_block.md`` template so an operator can reword
    # the wording in one file without touching Python. The
    # per-skill bullets below are formatted from the
    # runtime catalog.
    from magi.agent.prompts import load_skills_block_template
    lines = ["", *load_skills_block_template().splitlines(), ""]
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
