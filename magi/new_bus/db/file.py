"""FileStore — directory-scoped structured file I/O with hot-reload caching.

``FileStore`` is the file-system counterpart to
:class:`~magi.new_bus.db.engine.EngineFactory`: EngineFactory wraps a
SQLAlchemy ``Engine``, FileStore wraps a local directory.  Both serve
as backends for *Books* (see :mod:`magi.new_bus.library.file`).

Each supported extension (``.md``, ``.yaml``, ``.yml``, ``.json``,
``.txt``) maps to a :class:`Format` codec.  Reads are *auto-detected*
— ``store.read("soul")`` finds ``soul.md``, decodes through
:class:`TextFormat`, and caches the decoded object.  Writes are
*atomic* (``tempfile.mkstemp`` + ``os.replace``) so a reader never
sees a half-written file.

Hot-reload: every ``read()`` does a single ``Path.stat()`` and compares
``(st_mtime_ns, st_size)`` against the cached fingerprint.  Mismatch
evicts + re-reads + re-decodes.  The per-call stat is microseconds.

Thread-safety: the cache is protected by a :class:`threading.Lock`;
double-checked locking keeps the fast path lock-free.  Path safety:
names must be relative and may not contain ``..`` segments.
"""

from __future__ import annotations

import json as _json
import logging
import os
import tempfile
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger("magi.new_bus.db.file")

# Suffix search order for auto-detection in :meth:`FileStore.read`.
# ``.md`` first (most common for prompts), then YAML variants,
# then JSON, then plain text.
_READ_SUFFIXES: Final[tuple[str, ...]] = (".md", ".yaml", ".yml", ".json", ".txt")


# =========================================================================
# Format codecs
# =========================================================================


class Format(ABC):
    """A file-format binding: one extension → load/dump codec.

    Subclass and register in :class:`FileStore`'s *formats* tuple
    to add a new on-disk format without touching the store itself.
    """

    #: Canonical file extension (no leading dot).  e.g. ``"md"``.
    extension: str

    @abstractmethod
    def load(self, text: str) -> Any:
        """Decode on-disk text into an in-memory object."""

    @abstractmethod
    def dump(self, value: Any) -> str:
        """Encode an in-memory object into on-disk text."""

    #: If ``True``, :class:`FileStore` strips the raw bytes before
    #: calling :meth:`load`.  Markdown wants leading/trailing
    #: whitespace stripped; YAML and JSON are already canonical.
    strip_on_read: bool = False


class TextFormat(Format):
    """Pass-through ``str`` format for ``.md`` (stripped) and ``.txt`` (raw)."""

    def __init__(self, *, extension: str, strip: bool) -> None:
        self.extension = extension
        self.strip_on_read = strip

    def load(self, text: str) -> str:  # type: ignore[override]
        return text.strip() if self.strip_on_read else text

    def dump(self, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError(
                f"{self.extension} format expects str, got {type(value).__name__}"
            )
        return value


class YamlFormat(Format):
    """YAML codec (uses :func:`yaml.safe_load` / :func:`yaml.safe_dump`)."""

    extension: str = "yaml"

    def __init__(self, *, extension: str = "yaml") -> None:
        self.extension = extension

    def load(self, text: str) -> Any:
        import yaml
        return yaml.safe_load(text)

    def dump(self, value: Any) -> str:
        import yaml
        return yaml.safe_dump(
            value, allow_unicode=True, sort_keys=False, default_flow_style=False,
        )


class JsonFormat(Format):
    """JSON codec (stdlib :mod:`json`)."""

    extension: str = "json"

    def load(self, text: str) -> Any:
        return _json.loads(text)

    def dump(self, value: Any) -> str:
        return _json.dumps(value, ensure_ascii=False, indent=2)


#: Default format set: markdown / YAML / JSON / plain text.
DEFAULT_FORMATS: Final[tuple[Format, ...]] = (
    TextFormat(extension="md", strip=True),
    YamlFormat(extension="yaml"),
    YamlFormat(extension="yml"),
    JsonFormat(),
    TextFormat(extension="txt", strip=False),
)


# =========================================================================
# Errors
# =========================================================================


class FileStoreError(Exception):
    """Base for every error :class:`FileStore` raises."""


class FormatError(FileStoreError):
    """Raised when a file's extension has no registered :class:`Format`."""


class PathError(FileStoreError):
    """Raised when a *name* is absolute or contains ``..`` traversal."""


# =========================================================================
# Cache
# =========================================================================


@dataclass(frozen=True, slots=True)
class _Entry:
    """A single cache slot: decoded value + on-disk version fingerprint."""

    value: Any
    mtime_ns: int
    size: int


# =========================================================================
# FileStore
# =========================================================================


class FileStore:
    """Directory-backed, format-aware read/write service.

    Parallel to :class:`~magi.new_bus.db.engine.EngineFactory` for
    files.  Wraps a single root directory, knows about a fixed set
    of extensions→format bindings, and serves ``read`` / ``write`` /
    ``delete`` / ``list`` / ``exists`` with hot-reload caching.

    **Auto-detection**: :meth:`read` accepts names *without* an
    extension (e.g. ``"soul"``) and tries ``.md`` → ``.yaml`` →
    ``.yml`` → ``.json`` → ``.txt`` in order.  This is the
    ergonomic default for prompt-style workloads.

    **Atomic write**: :meth:`write` uses ``tempfile.mkstemp`` +
    ``os.replace`` so concurrent readers always see a complete file.

    Construction::

        store = FileStore(Path("/var/magi/prompts"))
        soul = store.read_text("soul")           # → str  (auto-detects .md)
        replies = store.read("bot_replies")       # → dict (auto-detects .yaml)

    The store is **process-local** (in-memory cache + ``threading.Lock``).
    Cross-process changes are picked up naturally via the mtime/size check.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        formats: tuple[Format, ...] = DEFAULT_FORMATS,
        create_root: bool = True,
    ) -> None:
        self._root = Path(root).resolve()
        # ext (no dot) → Format
        self._formats: dict[str, Format] = {f.extension: f for f in formats}
        self._lock = threading.Lock()
        # name → _Entry  (name is the caller-supplied key, no extension)
        self._cache: dict[str, _Entry] = {}
        # resolved path → (mtime_ns, size)
        self._versions: dict[Path, tuple[int, int]] = {}
        if create_root:
            self._root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # public properties
    # ------------------------------------------------------------------

    @property
    def root(self) -> Path:
        """Absolute, resolved root directory."""
        return self._root

    @property
    def formats(self) -> tuple[Format, ...]:
        """Immutable list of formats this store serves."""
        return tuple(self._formats.values())

    # ------------------------------------------------------------------
    # read
    # ------------------------------------------------------------------

    def read(self, name: str) -> Any:
        """Read *name* (no extension needed) and return its decoded content.

        Auto-detects the file: tries ``.md`` → ``.yaml`` → ``.yml`` →
        ``.json`` → ``.txt`` in order.  The first match is decoded
        through the corresponding :class:`Format` and cached.

        Hot-reload: a single ``Path.stat()`` per call (microseconds)
        compared against the cached ``(mtime_ns, size)`` fingerprint.
        Mismatch evicts + re-reads + re-decodes.

        Raises:
            FileNotFoundError: no matching file found.
            FormatError: the file's extension has no registered format.
            PathError: *name* is absolute or contains ``..``.
        """
        resolved = self._resolve(name)
        fmt = self._format_for(resolved)

        # Fast path: stat + cache lookup (lock-free).
        try:
            st = resolved.stat()
        except OSError as exc:
            raise FileNotFoundError(
                f"FileStore: {name!r} ({resolved}) not readable: {exc}"
            ) from exc
        version = (st.st_mtime_ns, st.st_size)

        cached = self._cache.get(name)
        if cached is not None and (cached.mtime_ns, cached.size) == version:
            return cached.value

        # Slow path: re-read + decode + cache (double-checked locking).
        with self._lock:
            cached = self._cache.get(name)
            if cached is not None and (cached.mtime_ns, cached.size) == version:
                return cached.value
            try:
                text = resolved.read_text(encoding="utf-8")
            except OSError as exc:
                raise FileNotFoundError(
                    f"FileStore: {name!r} vanished mid-read: {exc}"
                ) from exc
            if fmt.strip_on_read:
                text = text.strip()
            value = fmt.load(text)
            self._cache[name] = _Entry(
                value=value, mtime_ns=version[0], size=version[1],
            )
            self._versions[resolved] = version

        logger.debug(
            "FileStore reloaded %s (mtime_ns=%d size=%d)",
            resolved.name, version[0], version[1],
        )
        return value

    def read_text(self, name: str) -> str:
        """Convenience: :meth:`read` + assert ``str`` result.

        Raises :class:`TypeError` if the format decoded into
        something other than ``str`` (e.g. YAML returned a mapping).
        """
        value = self.read(name)
        if not isinstance(value, str):
            raise TypeError(
                f"FileStore: {name!r} decoded into {type(value).__name__}, not str"
            )
        return value

    # ------------------------------------------------------------------
    # write
    # ------------------------------------------------------------------

    def write(
        self, name: str, value: Any, *, suffix: str = ".md",
    ) -> Path:
        """Atomically write *value* to ``<root>/<name><suffix>``.

        The format is inferred from *suffix* (e.g. ``.yaml`` →
        :class:`YamlFormat`).  The write is atomic: dump to a sibling
        tempfile, then :func:`os.replace` it over the target — a reader
        sees either the old or the new content, never a half-written file.

        The in-memory cache for *name* is evicted so the next
        :meth:`read` picks up the new content.

        Returns the resolved :class:`Path` written.
        """
        resolved = self._root / f"{name}{suffix}"
        fmt = self._format_for(resolved)

        text = fmt.dump(value)
        resolved.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write: tempfile in same directory → os.replace.
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{resolved.name}.",
            suffix=".tmp",
            dir=str(resolved.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp_path, resolved)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        # Evict cache — next read will stat + re-decode.
        with self._lock:
            self._cache.pop(name, None)
            self._versions.pop(resolved, None)

        logger.debug("FileStore wrote %s (%d bytes)", resolved, len(text))
        return resolved

    def write_structured(
        self, name: str, data: dict[str, Any] | list[Any],
    ) -> Path:
        """Convenience: serialize *data* as YAML and :meth:`write`.

        Equivalent to ``store.write(name, data, suffix=".yaml")``.
        """
        return self.write(name, data, suffix=".yaml")

    # ------------------------------------------------------------------
    # delete
    # ------------------------------------------------------------------

    def delete(self, name: str) -> bool:
        """Delete the file matching *name* if it exists.

        Returns ``True`` if a file was deleted.  Does **not** raise
        on missing — callers can delete idempotently.  Always evicts
        the cache entry, even if the file was already gone.
        """
        try:
            resolved = self._resolve(name)
        except (FileNotFoundError, PathError):
            return False

        with self._lock:
            existed = resolved.exists()
            if existed:
                try:
                    resolved.unlink()
                except OSError:
                    self._cache.pop(name, None)
                    self._versions.pop(resolved, None)
                    raise
            self._cache.pop(name, None)
            self._versions.pop(resolved, None)
        return existed

    # ------------------------------------------------------------------
    # query
    # ------------------------------------------------------------------

    def list(self, pattern: str = "*") -> list[str]:
        """Return deduplicated file *names* (stem, no extension).

        Searches recursively under ``root``.  Only files with
        registered extensions are included.  Names are relative
        paths with forward slashes (e.g. ``"context/memory_block"``).

        *pattern* is a :func:`~pathlib.Path.rglob` pattern (default ``"*"``).
        """
        stems: set[str] = set()
        for p in self._root.rglob(pattern):
            if not p.is_file():
                continue
            ext = p.suffix.lstrip(".")
            if ext not in self._formats:
                continue
            rel = p.relative_to(self._root)
            stems.add(str(rel.with_suffix("")).replace("\\", "/"))
        return sorted(stems)

    def exists(self, name: str) -> bool:
        """Return ``True`` if a file matching *name* exists on disk."""
        try:
            self._resolve(name)
            return True
        except (FileNotFoundError, PathError):
            return False

    def invalidate(self, name: str | None = None) -> None:
        """Force-evict cache entries.

        - ``name=None`` → evict all entries.
        - ``name="soul"`` → evict only that entry.
        """
        with self._lock:
            if name is None:
                self._cache.clear()
                self._versions.clear()
            else:
                self._cache.pop(name, None)
                try:
                    resolved = self._resolve(name)
                    self._versions.pop(resolved, None)
                except (FileNotFoundError, PathError):
                    pass

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _resolve(self, name: str) -> Path:
        """Validate *name* and find the concrete on-disk path.

        Auto-detects extension: tries ``.md`` → ``.yaml`` → ``.yml`` →
        ``.json`` → ``.txt``.  Rejects absolute paths and ``..``
        segments for security.

        Raises:
            FileNotFoundError: no matching file exists.
            PathError: unsafe name.
        """
        if not isinstance(name, str) or not name:
            raise PathError(f"name must be a non-empty string, got {name!r}")

        p = Path(name)
        if p.is_absolute():
            raise PathError(f"name must be relative, got {name!r}")
        if any(part == ".." for part in p.parts):
            raise PathError(f"name must not contain '..' segments, got {name!r}")

        # Auto-detect: try each suffix in priority order.
        for suffix in _READ_SUFFIXES:
            candidate = self._root / f"{name}{suffix}"
            if candidate.is_file():
                return candidate

        # If the name already has a recognised extension, try exact match
        # (supports callers that prefer "soul.md" style).
        if p.suffix and p.suffix.lstrip(".") in self._formats:
            candidate = self._root / name
            if candidate.is_file():
                return candidate

        raise FileNotFoundError(
            f"FileStore: {name!r} not found in {self._root} "
            f"(tried suffixes: {', '.join(_READ_SUFFIXES)})"
        )

    def _format_for(self, resolved: Path) -> Format:
        """Map the file's extension to a :class:`Format`."""
        ext = resolved.suffix.lstrip(".")
        try:
            return self._formats[ext]
        except KeyError as exc:
            raise FormatError(
                f"no format registered for extension {ext!r} "
                f"(known: {sorted(self._formats)!r})"
            ) from exc


__all__ = [
    "DEFAULT_FORMATS",
    "FileStore",
    "FileStoreError",
    "Format",
    "FormatError",
    "JsonFormat",
    "PathError",
    "TextFormat",
    "YamlFormat",
]
