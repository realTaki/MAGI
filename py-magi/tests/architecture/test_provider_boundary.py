"""Boundary rule: ``providers`` does not import ``agent.*``.

Phase F — the new top-level package owns every LLM call. The
worker must NEVER reach back into ``agent`` for prompt
assembly, system-prompt assembly, ``ChatMessage`` construction,
``maybe_compact`` execution, or any other agent-loop concern.
The agent side has its own dedicated helpers under
:meth:`agent._step_helpers` that the agent loop invokes
*before* publishing a :class:`LLMJob` onto the queue.

A regression here would re-couple the provider worker to the
agent package — the very thing Phase D just uncoupled.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROVIDERS_ROOT = REPO_ROOT / "magi" / "providers"

_FORBIDDEN_PREFIXES = ("agent",)


def _iter_python_files(root: Path):
    yield from root.rglob("*.py")


def _imports_module(path: Path) -> list[tuple[str, int]]:
    """Return ``(imported_module, line_number)`` pairs from ``path``.

    Walks every ``import X`` and ``from X import Y`` statement; the
    result is the *imported* package path (``X``) so a downstream
    check can compare against the forbidden prefix list.
    """
    text = path.read_text()
    tree = ast.parse(text, filename=str(path))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue  # relative import like ``from . import x``
            module = node.module
            # Resolve ``from . import x`` / ``from ..foo import y``
            # by walking the leading-dot count and prefixing the
            # current package path. The simpler test only cares
            # about absolute prefixes, so we leave relative imports
            # as-is.
            if node.level:
                # ``from .X import Y`` -> parent_pkg.X
                "." * node.level
                # Walk up from the file's package path.
                pkg_parts = path.relative_to(REPO_ROOT).parent.parts
                up = len(pkg_parts) - node.level + 1
                if up < 0:
                    continue
                ancestor = ".".join(pkg_parts[:up])
                module = (ancestor + "." + module.lstrip(".")) if module else ancestor
            found.append((module, node.lineno))
    return found


def test_providers_does_not_import_agent() -> None:
    violations: list[str] = []
    for path in _iter_python_files(PROVIDERS_ROOT):
        for module, line in _imports_module(path):
            for prefix in _FORBIDDEN_PREFIXES:
                if module == prefix or module.startswith(prefix + "."):
                    rel = path.relative_to(REPO_ROOT)
                    violations.append(f"{rel}:{line}  imports  {module}")
                    break
    if violations:
        [f"  {violations[i]}\n  ..." for i in range(0, len(violations), 1)]
        msg = "\n".join(
            [
                "Forbidden cross-package imports from providers:",
                *violations,
                "",
                "  The provider worker must NOT reach into agent;",
                "  agent-side concerns (system prompt, messages, tools) live",
                "  in agent._step_helpers and run *before* the job is",
                "  enqueued onto the providers queue.",
            ]
        )
        pytest.fail(msg)
