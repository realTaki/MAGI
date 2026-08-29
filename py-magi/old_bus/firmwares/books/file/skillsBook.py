"""SkillsBook — workspace-managed skill registry, file-backed.

Skills are directories containing a ``SKILL.md`` file. The book scans
two roots at construction and merges them with operator-overrides-bundle
semantics:

  - **bundle** root — ``<magi>/skills/`` (the image-shipped default catalog).
    Resolved by the book itself via :func:`_resolve_bundle_skills_dir`
    (anchors on the ``magi`` package; falls back to deriving from
    ``__file__`` when the package is not importable in the usual way
    — tests / zip-app installs).

  - **operator** root — ``<workspace>/skills/`` (the deployer's
    customised catalog). Resolved by the caller from
    :func:`startup.paths.resolve_skills_dir` and passed in.

The package bundle is used only to seed missing directories into a workspace.
Runtime reads thereafter use the workspace root.

Frontmatter shape (the bit between two ``---`` lines)::

    ---
    name: web_lookup                # required, must match the dir name
    description: 互联网检索 ......   # required, used in the system block
    version: "1.0"                  # optional
    license: "..."                  # optional
    allowed-tools:                  # optional YAML list of strings
      - bash
      - read_file
    metadata:                       # optional free-form {key: value}
      domain: ops
    ---

Body retrieval (Progressive Disclosure Level 3) is the book's job,
not the tool's: :meth:`SkillsBook.read_body` strips the frontmatter
(stripped at load so the LLM doesn't re-see ``name``/``description``),
rewrites relative file references in the body to absolute paths,
prepends a "Skill Root Directory" hint, byte-truncates at
``_BODY_MAX_BYTES`` with UTF-8-safe boundary, and returns a
:class:`SkillBody` carrying the content + mtime + truncated flag.

Failure modes (graceful, never raise at scan time):

  - duplicate ``name`` across roots → operator overrides bundle, silent
  - malformed frontmatter → skip the skill, log a warning
  - missing ``name`` key → use the directory basename
  - missing/empty ``description`` → skip, log a warning
  - missing bundle root → empty registry, log INFO
  - invalid directory name → skip, log a warning

Hot-reload: every public read (``get`` / ``list`` / ``exists`` /
``read_body``) checks the on-disk fingerprint of both roots and
re-scans when it changes. The fingerprint is the sorted tuple of
``(skill_dir_name, skill_md_mtime_ns, skill_md_size)`` for every
``SKILL.md`` under each root — that single tuple catches *all* of:

  - skill dir added / removed
  - ``SKILL.md`` created / deleted inside an existing dir
  - ``SKILL.md`` content edited (mtime or size delta)

Re-scan cost is bounded: one ``stat()`` per skill dir per public
call (microseconds). Body reads were already always-fresh —
:meth:`SkillsBook.read_body` reads from disk to capture the mtime
the API surface returns; this just brings the registry in line.

Thread safety: rebuild + registry read are guarded by a single
:class:`threading.Lock`. Tests + workers can call any public
method from any thread without external synchronisation.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from old_bus.bases.db.file import FileShelf

logger = logging.getLogger("bus.firmwares.books.file.skills_book")

_SKILL_FILENAME = "SKILL.md"

# 1-2 sentence description is the sweet spot — fits the system-prompt
# block without bloating, tells the LLM when to reach for ``load_skill``.
_DESCRIPTION_MAX = 240
_NAME_RE = re.compile(r"^[a-zA-Z0-9_.\-]{1,64}$")

# Cap on body size. The agent loop truncates at 8 KB regardless
# (see ``agent.py``:642-645); the difference is the *operator-visible*
# content: an LLM that sees a truncation marker can decide to ask for
# a specific section next turn.
_BODY_MAX_BYTES = 32 * 1024


class SkillBookError(Exception):
    """Base for every error :class:`SkillsBook` raises."""


class SkillNotFound(SkillBookError):
    """Raised by :meth:`SkillsBook.read_body` when *name* is unknown."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SkillMeta:
    """One row in the registry.

    The body is **not** stored here. Only ``name`` and ``description``
    are needed for the system-prompt skills block. The body is read on
    demand by :meth:`SkillsBook.read_body`.

    The three optional frontmatter fields (``license`` /
    ``allowed_tools`` / ``metadata``) are read but not yet acted on —
    they're stashed here so a future feature (allow-list, audit log,
    license attribution) doesn't need a schema change.
    """

    name: str  # skill 名（与目录名一致）
    description: str  # 一句话描述（暴露给 LLM）
    path: Path  # SKILL.md 文件路径
    version: str | None = None  # 来自 frontmatter 的版本号
    license: str | None = None  # 来自 frontmatter 的 license
    # ``allowed-tools`` in the frontmatter is a YAML list (Anthropic
    # skill spec). We store as ``list[str]``; missing / non-list
    # frontmatter values become ``None`` so callers can use
    # ``is None`` as the "no restriction" check.
    allowed_tools: list[str] | None = None  # 允许使用的工具列表
    # ``metadata`` is a free-form ``{key: value}`` map.
    metadata: dict[str, str] | None = None  # 自定义元数据


@dataclass(frozen=True, slots=True, kw_only=True)
class SkillBody:
    """Result of :meth:`SkillsBook.read_body` — content + provenance.

    The API router (``channels/api/skills.py``) needs all three fields
    to render its ``SkillBodyOut`` Pydantic response; the LLM tool
    (``tools/skills/load_skill.py``) uses only ``content`` but the
    dataclass is the single shape so neither side has to know which
    fields the other consumes.
    """

    content: str  # skill 正文（已去除 frontmatter、改写路径）
    mtime: datetime  # 文件 mtime
    truncated: bool  # True=正文超过大小上限被截断


# ──────────────────────────────────────────────────────────────────────── #
# Frontmatter parsing
# ──────────────────────────────────────────────────────────────────────── #


def _parse_frontmatter(raw: str) -> tuple[dict, str, dict]:
    """Extract YAML frontmatter at the file's head + return the body.

    Returns a 3-tuple ``(str_dict, body, typed_dict)``:

    - ``str_dict`` — every value coerced to ``str`` via ``str(v)``.
      The flat ``key: value`` shape v0 callers (the system-prompt
      metadata block) read from. Stable across PyYAML-present /
      PyYAML-missing paths.
    - ``body`` — the markdown after the closing ``---``.
    - ``typed_dict`` — best-effort PyYAML-typed parse (when PyYAML
      is installed). The new optional fields
      (:attr:`SkillMeta.allowed_tools` / ``metadata``) read from
      here. Falls back to an empty dict when PyYAML is missing.
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
    # Fallback: ``key: value`` per line, value is the rest of the
    # line stripped. ``v: 'foo bar'`` (quoted strings) is handled by
    # stripping the quotes. Nested values aren't handled — we don't
    # use any.
    for raw_line in fm_lines:
        line = raw_line.rstrip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        fm[key.strip()] = value
    return fm, body, typed


def _skill_name_from_dir(skill_dir: Path) -> str | None:
    """Validate the directory name is a usable skill name.

    Returns ``None`` for invalid names so callers can log + skip
    without translating the regex themselves.
    """
    name = skill_dir.name
    if not _NAME_RE.match(name):
        logger.warning(
            "skills: %s has invalid name %r, skipping",
            skill_dir,
            name,
        )
        return None
    return name


def _truncate_description(text: str) -> str:
    """Single-line, max ``_DESCRIPTION_MAX`` chars, '…' suffix."""
    text = " ".join(text.split())
    if len(text) > _DESCRIPTION_MAX:
        text = text[: _DESCRIPTION_MAX - 1] + "…"
    return text


def _coerce_str_list(value: Any) -> list[str] | None:
    """Coerce a frontmatter ``allowed-tools`` value to ``list[str]`` or ``None``.

    - ``None`` (key absent) → ``None``
    - YAML list of strings → ``[str, ...]``
    - Anything else (str, int, dict) → ``None`` silently. The system
      doesn't crash on weird frontmatter; it just ignores the field.
    """
    if value is None:
        return None
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return None


def _coerce_str_dict(value: Any) -> dict[str, str] | None:
    """Coerce a frontmatter ``metadata`` value to ``dict[str, str]`` or ``None``."""
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items() if k is not None}
    return None


# ──────────────────────────────────────────────────────────────────────── #
# Body path processing — Progressive Disclosure Level 3
# ──────────────────────────────────────────────────────────────────────── #


def _skill_root_dir_line(skill_dir: Path) -> str:
    """The first line of the body the LLM sees when it ``load_skill``s
    a skill. Tells the LLM where the sibling files live so it can
    compose absolute paths itself if it needs to.

    Kept as a separate helper so the wording can be tweaked without
    touching the path-rewriting logic.
    """
    return (
        f"**Skill Root Directory:** `{skill_dir}`\n\n"
        f"All files and references in this skill are "
        f"relative to this directory.\n\n---\n\n"
    )


def _process_skill_paths(
    body: str,
    skill_dir: Path,
) -> str:
    """Rewrite relative file references in the skill body to absolute
    paths so the LLM can ``read_file`` them directly.

    Three patterns:

      1. ``scripts/foo.py`` / ``references/bar.md`` — plain relative
         paths. Resolved against ``skill_dir`` only if the file
         exists there.
      2. ``see reference.md`` / ``read forms.md`` — prose references
         to a sibling file. Same resolution rule.
      3. ``[`text`](relpath)`` — markdown links. ``./`` prefix
         stripped, then resolved.

    Each rewrite turns the relative reference into
    ``"<abs path> (use read_file to access)"`` so the LLM knows how
    to fetch the file.

    Resolution is **existence-checked** — a path that doesn't exist
    on disk is left alone. This avoids hallucinating non-existent
    files when the skill body mentions a file the deployer didn't
    ship.
    """

    # Pattern 1: directory-based relative paths
    # (``scripts/`` / ``references/`` / ``assets/``). The optional
    # non-capturing prefix group captures the command-style "python "
    # or " `", so a leading ``python scripts/foo.py`` becomes
    # ``python /abs/path/scripts/foo.py`` (prefix preserved).
    def _replace_dir_path(match: re.Match[str]) -> str:
        prefix = match.group(1) or ""
        rel = match.group(2)
        abs_path = skill_dir / rel
        if abs_path.exists():
            return f"{prefix}{abs_path}"
        return match.group(0)

    # The body is ``(?:(python\s+|\s`))?`` followed by a directory
    # prefix + path. Two alternatives inside the optional prefix
    # group: ``python `` (with trailing whitespace) or a leading
    # whitespace + backtick (inline-code form). ``\s``` is two
    # characters: whitespace then literal backtick.
    pattern_dirs = (
        r"(?:(python\s+|\s`))?"  # optional "python " or " `"
        r"((?:scripts|references|assets)/"  # one of the 3 dirs
        r"[^\s`)\]]+)"  # the rest of the path
    )
    body = re.sub(pattern_dirs, _replace_dir_path, body)

    # Pattern 2: prose references like "see reference.md" / "read
    # forms.md". Suffix is the trailing punctuation / whitespace we
    # want to preserve.
    def _replace_doc_path(match: re.Match[str]) -> str:
        prefix_word = match.group(1)
        filename = match.group(2)
        suffix = match.group(3) or ""
        abs_path = skill_dir / filename
        if abs_path.exists():
            return f"{prefix_word}`{abs_path}` (use read_file to access){suffix}"
        return match.group(0)

    pattern_docs = (
        r"\b(see|read|refer to|check)\s+"
        r"([a-zA-Z0-9_.\-]+\.(?:md|txt|json|yaml|yml))"
        r"([.,;:\s])"
    )
    body = re.sub(pattern_docs, _replace_doc_path, body, flags=re.IGNORECASE)

    # Pattern 3: markdown links — ``[`text`](relpath)``,
    # ``[text](relpath)``, with optional ``./`` prefix. Cap the path
    # segment at 200 chars to avoid runaway backtracking on weird
    # content.
    def _replace_md_link(match: re.Match[str]) -> str:
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


# ──────────────────────────────────────────────────────────────────────── #
# SkillsBook
# ──────────────────────────────────────────────────────────────────────── #


class SkillsBook:
    """Read-side registry for workspace-managed SKILL.md files.

    Built on two :class:`~bus.bases.db.file.FileShelf` instances
    (bundle + operator). The shelves give us safe path resolution
    underneath; the registry itself is hot-reloaded on every public
    read via :meth:`_fresh_registry`.

    Lookup contract:

      - :meth:`get` / :meth:`list` / :meth:`exists` / :meth:`read_body`
        all call :meth:`_fresh_registry` first, which re-scans if the
        on-disk fingerprint (per-skill-dir tuple of name + mtime +
        size of SKILL.md) has changed. New / removed / edited
        skills become visible without a process restart.
      - :meth:`read_body` reads ``meta.path`` directly to capture the
        on-disk mtime for the API response; it bypasses any cache
        but matches the previous behaviour.

    Thread safety: a single :class:`threading.Lock` guards both the
    fingerprint check + re-scan and the registry read that follows.
    Public methods are safe to call from any thread.
    """

    def __init__(
        self,
        bundle: FileShelf | None,
        operator: FileShelf,
    ) -> None:
        self._bundle = bundle
        self._operator = operator
        self._registry: dict[str, SkillMeta] = {}
        # Per-root fingerprints; ``()`` until the first scan. The
        # initial scan writes both, so the first public call is a
        # no-op compare.
        self._bundle_fp: tuple = ()
        self._operator_fp: tuple = ()
        self._lock = threading.Lock()
        self._fresh_registry()  # populate registry + fingerprints at construction

    # ─── public surface ────────────────────────────────────────────────

    def list(self) -> list[SkillMeta]:
        """Sorted by skill name for stable UI ordering.

        Hot-reloads the registry first if the on-disk fingerprint of
        either root has changed since the last scan.
        """
        return sorted(self._fresh_registry().values(), key=lambda s: s.name)

    def get(self, name: str) -> SkillMeta | None:
        """Return the :class:`SkillMeta` for *name*, or ``None``.

        Hot-reloads the registry first.
        """
        return self._fresh_registry().get(name)

    def exists(self, name: str) -> bool:
        """``True`` iff *name* is registered. Hot-reloads first."""
        return name in self._fresh_registry()

    def read_body(self, name: str) -> SkillBody:
        """Read the full markdown body for *name*, ready for the LLM.

        Hot-reloads the registry first, then runs the standard
        pipeline (matches the pre-refactor
        ``SkillLoaderTool._read_skill_body``):

          1. Look up :class:`SkillMeta`; raise :class:`SkillNotFound` if missing.
          2. Byte-read + UTF-8-decode the SKILL.md, capture mtime via ``stat``.
          3. Byte-truncate at :data:`_BODY_MAX_BYTES`, walking back over
             UTF-8 continuation bytes so the cut never splits a rune.
          4. Strip the YAML frontmatter (the LLM already saw
             ``name`` / ``description`` in the system prompt).
          5. Rewrite relative file references in the body to absolute
             paths (:func:`_process_skill_paths`).
          6. Prepend the "Skill Root Directory" hint.

        Returns a :class:`SkillBody` carrying the content + UTC mtime +
        truncation flag. The :attr:`SkillBody.truncated` flag is True
        iff the on-disk body exceeded :data:`_BODY_MAX_BYTES`.
        """
        meta = self.get(name)
        if meta is None:
            raise SkillNotFound(f"no skill named {name!r} is registered")
        skill_path = meta.path
        try:
            raw = skill_path.read_bytes()
            st = skill_path.stat()
        except OSError as exc:
            raise SkillBookError(f"failed to read skill body for {name!r}: {exc}") from exc

        truncated = False
        truncated_marker = ""
        if len(raw) > _BODY_MAX_BYTES:
            # Truncate at byte boundary, then walk back to the start
            # of the last code point so the truncated string is valid
            # UTF-8. Continuation bytes match ``10xxxxxx``.
            truncated = True
            truncated_bytes = raw[:_BODY_MAX_BYTES]
            while truncated_bytes and (truncated_bytes[-1] & 0xC0) == 0x80:
                truncated_bytes = truncated_bytes[:-1]
            raw = truncated_bytes
            truncated_marker = (
                f"\n\n…[truncated at {_BODY_MAX_BYTES} bytes; the rest of "
                f"the skill is unavailable through this tool]"
            )

        text = raw.decode("utf-8", errors="replace")

        # Strip the YAML frontmatter. The closing ``---`` is the
        # second ``---`` line; everything after is the body.
        if text.startswith("---"):
            lines = text.splitlines()
            close_idx = -1
            for i in range(1, len(lines)):
                if lines[i].strip() == "---":
                    close_idx = i
                    break
            if close_idx != -1:
                text = "\n".join(lines[close_idx + 1 :])

        # Run the path rewriter BEFORE prepending the root line, so
        # the rewriter doesn't accidentally rewrite the absolute path
        # we just inserted.
        text = _process_skill_paths(text, skill_path.parent)
        content = _skill_root_dir_line(skill_path.parent) + text + truncated_marker

        mtime = datetime.fromtimestamp(st.st_mtime, tz=UTC)
        return SkillBody(content=content, mtime=mtime, truncated=truncated)

    # ─── internal ─────────────────────────────────────────────────────

    def _scan(self) -> None:
        """Build the merged registry. Idempotent — calling
        :meth:`__init__` twice on the same roots behaves the same as
        once (used by tests).

        Must be called with :attr:`_lock` held (see
        :meth:`_fresh_registry`). Two passes — bundle first (the
        defaults), then operator (overrides bundle entries with the
        same name; no warning, that is the normal customisation flow).
        """
        self._registry.clear()
        bundle_count = self._scan_root(self._bundle, source="bundle")
        operator_count_before = len(self._registry)
        self._scan_root(self._operator, source="operator")
        operator_count = len(self._registry) - operator_count_before
        logger.info(
            "skills: %d loaded (%d from bundle, %d from operator)",
            len(self._registry),
            bundle_count,
            operator_count,
        )

    def _fresh_registry(self) -> dict[str, SkillMeta]:
        """Return the registry, re-scanning first if either root changed.

        Fingerprint compute + compare + optional re-scan + registry
        read are all guarded by a single :attr:`_lock` acquisition so
        callers see a consistent snapshot.  Public methods
        (:meth:`list` / :meth:`get` / :meth:`exists`) delegate here.

        Fast path (no filesystem change): one ``stat()`` per skill
        dir (microseconds) + lock acquire.  Slow path: full re-scan.
        """
        bundle_fp = self._root_fingerprint(self._bundle)
        operator_fp = self._root_fingerprint(self._operator)
        with self._lock:
            if self._bundle_fp != bundle_fp or self._operator_fp != operator_fp:
                self._scan()
                self._bundle_fp = bundle_fp
                self._operator_fp = operator_fp
            return self._registry

    def _root_fingerprint(self, shelf: FileShelf | None) -> tuple:
        """Sorted tuple of ``(dir_name, mtime_ns, size)`` per SKILL.md.

        Missing root → ``()``. Missing/unreadable ``SKILL.md`` is
        silently skipped here (will be reported as a scan warning
        by :meth:`_scan_one` if the re-scan actually runs). ``iterdir``
        mid-write races are caught by :func:`OSError` → ``()``.
        """
        if shelf is None:
            return ()
        root = shelf.root
        if not root.is_dir():
            return ()
        try:
            entries = list(root.iterdir())
        except OSError:
            return ()
        out: list[tuple[str, int, int]] = []
        for skill_dir in entries:
            if not skill_dir.is_dir():
                continue
            skill_path = skill_dir / _SKILL_FILENAME
            try:
                st = skill_path.stat()
            except OSError:
                continue
            out.append((skill_dir.name, st.st_mtime_ns, st.st_size))
        return tuple(sorted(out))

    def _scan_root(self, shelf: FileShelf | None, *, source: str) -> int:
        """Scan one shelf's root, register its skills, return count.

        Missing or non-directory root → 0 skills, logged at
        INFO/WARN by severity. Operator with no ``skills/`` subdir
        yet is the common case on a fresh deploy — INFO, not WARN,
        so the boot log stays clean.
        """
        if shelf is None:
            return 0
        root = shelf.root
        if not root.exists():
            level = logger.info if source == "operator" else logger.warning
            level(
                "skills: %s root %s does not exist; no skills from %s",
                source,
                root,
                source,
            )
            return 0
        if not root.is_dir():
            logger.warning(
                "skills: %s root %s is not a directory; skipping",
                source,
                root,
            )
            return 0
        before = len(self._registry)
        for skill_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            self._scan_one(skill_dir, source=source)
        loaded = len(self._registry) - before
        logger.info(
            "skills: %d loaded from %s root %s",
            loaded,
            source,
            root,
        )
        return loaded

    def _scan_one(self, skill_dir: Path, *, source: str) -> None:
        """Read + register one skill directory.

        Three ways to fail loudly-but-non-fatally:
          - invalid directory name (regex) → skip
          - missing SKILL.md → skip silently
          - OSError on read → skip + warn
          - malformed frontmatter / missing description → skip + warn
        """
        dir_name = _skill_name_from_dir(skill_dir)
        if dir_name is None:
            return  # already logged
        skill_path = skill_dir / _SKILL_FILENAME
        if not skill_path.is_file():
            logger.debug(
                "skills: %s has no SKILL.md; skipping",
                skill_dir,
            )
            return
        try:
            raw = skill_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "skills: failed to read %s: %s; skipping",
                skill_path,
                exc,
            )
            return
        fm, _body, typed = _parse_frontmatter(raw)
        # Name resolution priority: explicit frontmatter ``name`` →
        # directory basename. We DON'T enforce that the two match
        # (someday a deployer will want an alias); just warn on
        # mismatch.
        declared_name = fm.get("name", "").strip()
        if declared_name and declared_name != dir_name:
            logger.warning(
                "skills: %s declares name=%r but dir is %r; using the directory name",
                skill_path,
                declared_name,
                dir_name,
            )
        name = dir_name
        description_raw = fm.get("description", "").strip()
        if not description_raw:
            # An empty description wastes the system-prompt slot —
            # skip the skill rather than register it with a placeholder.
            logger.warning(
                "skills: %s has no description; skipping",
                skill_path,
            )
            return
        description = _truncate_description(description_raw)
        version = fm.get("version", "").strip() or None
        # Duplicate-name handling:
        #   - operator over bundle → silent. That is the normal
        #     "I edited web_lookup to my domain" flow; warning every
        #     boot would be noise.
        #   - bundle over operator (shouldn't happen given the load
        #     order, but defensive) → warning.
        #   - same-source duplicates → warning so the deployer sees
        #     the conflict.
        if name in self._registry:
            existing = self._registry[name]
            if (
                source == "operator"
                and self._bundle is not None
                and existing.path.is_relative_to(self._bundle.root)
            ):
                logger.debug(
                    "skills: operator %s overrides bundle %s for name %r",
                    skill_path,
                    existing.path,
                    name,
                )
            else:
                logger.warning(
                    "skills: duplicate name %r — overwriting previous definition at %s with %s",
                    name,
                    existing.path,
                    skill_path,
                )
        self._registry[name] = SkillMeta(
            name=name,
            description=description,
            path=skill_path,
            version=version,
            # Optional frontmatter fields. v0 doesn't act on them; a
            # future allow-list / audit / license-attribution feature
            # can read them without a schema change.
            license=typed.get("license"),
            allowed_tools=_coerce_str_list(typed.get("allowed-tools")),
            metadata=_coerce_str_dict(typed.get("metadata")),
        )


# ──────────────────────────────────────────────────────────────────────── #
# Default factory
# ──────────────────────────────────────────────────────────────────────── #


def _resolve_bundle_skills_dir() -> Path:
    """Return the path to the image-shipped skills bundle.

    Two-tier resolution, mirroring the prompts-bundle resolver:

    - Tier 1 anchors on the ``magi`` package itself (normal installs
      and wheel / zip-app installs).
    - Tier 2 derives from this file's ``__file__`` (when the package
      is not importable — e.g. ad-hoc test runs from a checkout).

    Lives here in the bus layer rather than in :mod:`startup.paths`
    so the bus does not depend on the composition root.
    See ARCHITECTURE_REVIEW_2026-08-10 P2.
    """
    try:
        import magi

        candidate = Path(magi.__file__).resolve().parent / "skills"
        if candidate.is_dir():
            return candidate
    except Exception:
        pass

    # Tier 2: ``__file__`` fallback. This module lives at
    # ``magi/bus/firmwares/books/file/skillsBook.py``; three levels up is ``magi/``.
    # ``+ "skills"`` gives ``magi/skills/``.
    return Path(__file__).resolve().parents[3] / "skills"


def build_default_skills_book(workspace_dir: Path) -> SkillsBook:
    """Seed missing defaults, then manage only ``<workspace>/skills/``.

    Skills have no Worker owner, so BUS initialization performs their
    idempotent package-to-workspace registration. Existing skill directories
    are never overwritten.
    """
    _seed_default_skills(workspace_dir / "skills")
    operator_shelf = FileShelf(workspace_dir / "skills", create_root=False)
    return SkillsBook(None, operator_shelf)


def _seed_default_skills(workspace_skills_dir: Path) -> None:
    """Copy each missing image-shipped skill directory into the workspace."""
    import shutil

    source_root = _resolve_bundle_skills_dir()
    workspace_skills_dir.mkdir(parents=True, exist_ok=True)
    if not source_root.is_dir():
        logger.warning("skills: bundled defaults root %s is missing", source_root)
        return
    for source in source_root.iterdir():
        if not source.is_dir() or not (source / _SKILL_FILENAME).is_file():
            continue
        target = workspace_skills_dir / source.name
        if target.exists():
            continue
        shutil.copytree(source, target)
        logger.info("skills: seeded default %s into %s", source.name, target)


__all__ = [
    "SkillBookError",
    "SkillBody",
    "SkillMeta",
    "SkillNotFound",
    "SkillsBook",
    "_NAME_RE",
    "build_default_skills_book",
]
