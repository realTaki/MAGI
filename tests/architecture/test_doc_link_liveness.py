"""Doc-link liveness: ``docs/*.md`` references must resolve against the live tree.

The 2026-08-10 architecture review (P2 #2) called for
"文档中的架构断言应由链接存在性检查和架构测试清单自动核验"
— the same drift that produced the deleted Hook subsystem
section in ``ARCHITECTURE.md`` could happen again unless the
docs are checked against the live tree at CI time.

This test extracts three kinds of references from each
``docs/*.md`` file and asserts they still resolve:

1. **File paths** like ``magi/foo/bar.py`` or
   ``tests/architecture/test_import_boundaries.py``. Resolved
   against the repo root. Missing files (retired modules,
   reorgs) fail the test.

2. **Module paths** like ``magi.bus.guild.base``. Importable
   modules fail the test.

3. **Relative file paths** like ``channels/worker.py`` (no
   prefix). Tries both the repo root and the ``magi/`` subtree
   before declaring a failure — many docs refer to paths
   relative to ``magi/`` even when the doc sits at
   ``docs/ARCHITECTURE.md``.

Scope policy
------------

- **Archived docs are skipped.** ``docs/ROADMAP.md`` is
  explicitly marked ``> ⚠️ ARCHIVED — historical planning
  document`` and references retired code paths by design.
  Re- **catching the drift in such docs would defeat the
  purpose of keeping them as historical context.**
- **The architecture review doc is also skipped.** It
  intentionally references the *previous* state of the code
  (e.g. the old ``_verify_signed_uid`` that the review
  flagged as broken) — that's its job.

Run with: ``pytest tests/architecture/test_doc_link_liveness.py``.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"


# --- regex catalogue ------------------------------------------------------

# File paths: ``a/b/c.py`` style, must contain a forward slash and
# end with a known extension. Catches ``magi/foo/bar.py`` and
# ``tests/architecture/test_x.py`` equally.
_FILE_RE = re.compile(
    r"`([a-zA-Z0-9_./-]+/(?:[a-zA-Z0-9_.-]+\.(?:py|md|toml|json|sh|yml|yaml|sql)))`"
)

# Module / attribute paths: dotted, must start with ``magi.``.
# Matches anything from ``magi.bus`` (module) to
# ``magi.bus.guild.base._cas_claim`` (function) to
# ``magi.bus.BusStore`` (class). The test logic below
# splits on the last dot: the prefix must be importable as
# a module; the suffix, when present, must resolve as an
# attribute on that module.
_PATH_RE = re.compile(
    r"`(magi\.(?:[a-zA-Z0-9_]+\.)+[a-zA-Z0-9_]+)`"
)

# Docs that intentionally document historical / retired state.
# These reference modules that were removed in the refactor
# that produced the current tree; chasing their references
# would defeat the historical-context purpose.
_SKIP_DOCS = {
    "docs/ROADMAP.md",
    "docs/ARCHITECTURE_REVIEW_2026-08-10.md",
    # ``id-naming-standard.md`` documents the rename history
    # (e.g. ``sessionBook.py → conversationBook.py``) — its
    # job is to record pre-rename references, so they're stale
    # by design.
    "docs/design/id-naming-standard.md",
    # ``business-flows.md`` explicitly notes
    # "旧的 ``magi.bus.BusStore`` / ``agent_turn_store`` /
    # ``magi.agent.step.run_agent_step`` / ``magic`` 表 等已删除"
    # — its references to retired code are part of the
    # documentation.
    "docs/business-flows.md",
    }


def _iter_doc_files() -> list[Path]:
    return sorted(p for p in DOCS_DIR.rglob("*.md"))


def _extract(text: str, regex: re.Pattern[str]) -> list[str]:
    """Return the unique references matched by *regex* in *text*."""
    seen: set[str] = set()
    for match in regex.findall(text):
        seen.add(match)
    return sorted(seen)


def _resolve_file(ref: str) -> bool:
    """A ``a/b/c.py`` reference is live if either the raw path
    or the same path under ``magi/`` exists on disk.

    Many docs use ``channels/worker.py`` to mean
    ``magi/channels/worker.py`` — the doc lives at
    ``docs/ARCHITECTURE.md`` which is one level deeper than
    ``magi/``, so the bare prefix is common.
    """
    raw = REPO_ROOT / ref
    if raw.exists():
        return True
    prefixed = REPO_ROOT / "magi" / ref
    return prefixed.exists()


# --- per-file tests -------------------------------------------------------


@pytest.mark.parametrize(
    "doc_path",
    [
        pytest.param(p, marks=pytest.mark.skipif(
            str(p.relative_to(REPO_ROOT)) in _SKIP_DOCS,
            reason="archived / historical doc — references are by design stale",
        ))
        for p in _iter_doc_files()
    ],
)
def test_file_paths_in_doc_exist(doc_path: Path) -> None:
    """Every ``a/b/c.py``-shaped reference in *doc_path*
    must resolve to a file on disk (under repo root or
    ``magi/`` subtree).
    """
    text = doc_path.read_text(encoding="utf-8")
    missing: list[str] = []
    for ref in _extract(text, _FILE_RE):
        if not _resolve_file(ref):
            missing.append(ref)
    assert not missing, (
        f"{doc_path.relative_to(REPO_ROOT)} references "
        f"non-existent files:\n  " + "\n  ".join(missing)
    )


@pytest.mark.parametrize(
    "doc_path",
    [
        pytest.param(p, marks=pytest.mark.skipif(
            str(p.relative_to(REPO_ROOT)) in _SKIP_DOCS,
            reason="archived / historical doc — references are by design stale",
        ))
        for p in _iter_doc_files()
    ],
)
def test_module_paths_in_doc_are_importable(doc_path: Path) -> None:
    """Every ``magi.x.y`` reference must resolve to either a
    module (the full path imports) or an attribute on a module
    (the dotted prefix imports, the suffix exists on it).

    Both classes (``magi.bus.Bus``) and functions
    (``magi.startup.runtime.run_magi``) are checked the same
    way: split on the last dot, import the prefix, verify
    the suffix as an attribute.
    """
    text = doc_path.read_text(encoding="utf-8")
    broken: list[tuple[str, str]] = []
    for ref in _extract(text, _PATH_RE):
        module_path, _, attr = ref.rpartition(".")
        if not module_path:
            continue
        try:
            mod = importlib.import_module(module_path)
        except ImportError as exc:
            broken.append((ref, f"module '{module_path}' not importable: {exc}"))
            continue
        if not hasattr(mod, attr):
            broken.append((ref, f"attribute '{attr}' missing on '{module_path}'"))
    assert not broken, (
        f"{doc_path.relative_to(REPO_ROOT)} references "
        f"unresolvable paths:\n  "
        + "\n  ".join(f"{ref}  ({reason})" for ref, reason in broken)
    )