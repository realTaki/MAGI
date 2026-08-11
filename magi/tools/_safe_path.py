"""Internal helper for resolving a tool-supplied path
safely against the workspace root.

Why a separate module: ``read_file`` and ``write_file`` (and
any future path-aware tool) need the same "this path is
relative to the workspace and must not escape it" check.
Inlining it into each tool would duplicate the logic and
make a subtle bug (e.g. forgetting ``resolve()`` before
the relative_to check) easy to ship.

Symlink handling: ``Path.resolve()`` follows symlinks. A
malicious operator who drops a symlink in the workspace
that points outside can trick the resolver into accepting
``"/etc/passwd"`` as long as the symlink path itself
starts under the workspace. That's a real attack surface
in a deploy where multiple admins share the workspace —
for v0 we trust the workspace owner (the ``runtime``
container runs as the operator's user) and accept the
residual risk. C8 hardening can swap in ``realpath()``
plus a containment check.
"""

from __future__ import annotations

import stat
from pathlib import Path

# Maximum length of the user-supplied path string. The
# Anthropic SDK doesn't bound input lengths and a 1 MB
# ``path`` would still pass the JSON-Schema validator;
# reject early to keep tool calls cheap.
_MAX_PATH_LEN = 1024


def safe_resolve(
    workspace: Path | str,
    requested: str,
    *,
    must_be_file: bool = True,
) -> Path:
    """Resolve ``requested`` against ``workspace`` and
    return the absolute, normalised path.

    Raises ``ValueError`` on any of:
      - empty / non-string path
      - path longer than ``_MAX_PATH_LEN``
      - path resolves outside the workspace tree
        (``ValueError("path escapes workspace")``)
      - path doesn't exist (when ``must_be_file=True``)
      - path is a directory (when ``must_be_file=True``)

    The caller decides what to do with the ``ValueError``
    — typically wrap in ``ToolResult(is_error=True, ...)``.
    """
    if not isinstance(requested, str) or not requested:
        raise ValueError("path must be a non-empty string")
    if len(requested) > _MAX_PATH_LEN:
        raise ValueError(f"path too long ({len(requested)} > {_MAX_PATH_LEN})")

    # ``resolve()`` against workspace so ``"foo/bar"`` lands
    # at ``<workspace>/foo/bar`` and absolute inputs are
    # rejected as out-of-tree.
    workspace_path = Path(workspace)
    candidate = (workspace_path / requested).resolve()
    workspace_resolved = workspace_path.resolve()

    # ``is_relative_to`` is the official containment check
    # (Python 3.9+); returns a bool directly.
    if not candidate.is_relative_to(workspace_resolved):
        raise ValueError(f"path escapes workspace: {requested!r}")

    if must_be_file:
        # Single ``stat()`` call instead of separate
        # ``exists()`` + ``is_dir()`` (two syscalls +
        # TOCTOU window).  Symlink-based escapes are a
        # known residual risk accepted for v0 (see module
        # docstring).
        try:
            st = candidate.stat()
        except FileNotFoundError as exc:
            raise ValueError(f"path does not exist: {requested!r}") from exc
        if stat.S_ISDIR(st.st_mode):
            raise ValueError(f"path is a directory, not a file: {requested!r}")

    return candidate
