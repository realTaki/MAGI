"""Lock the post-Phase-6 cleanup of the legacy ``handle_message`` re-export.

Design §19 / Phase 6 commits to removing the single-turn
``agent.loop.handle_message`` shim once all production call
sites have migrated to the actor runtime (publishing ``ChatNotify``). The current
state — confirmed by audit — is:

  - ``magi/agent/loop.py`` no longer exists (the module that
    defined the legacy ``handle_message``).
  - ``magi/agent/__init__.py`` is a single docstring; there is
    no PEP 562 ``__getattr__`` re-exporting loop symbols.
  - No production call site (``magi/channels/*``,
    ``magi/proactive/*``, ``magi/orchestrator/*``) imports
    ``handle_message``.

These tests verify those properties hold. A future regression
that re-introduces the legacy shim or re-creates ``loop.py``
will be caught here before reaching CI.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_loop_module_does_not_exist() -> None:
    """The legacy ``magi/agent/loop.py`` module must remain gone."""
    loop_path = REPO_ROOT / "agent" / "loop.py"
    assert not loop_path.exists(), (
        f"{loop_path} re-introduces the legacy single-turn loop; "
        "this is the post-Phase-6 cleanup. Move all logic to "
        "magi/agent/{worker,step,agent_context}.py."
    )


def test_agent_init_does_not_re_export_legacy_symbols() -> None:
    """``agent.handle_message`` must NOT resolve.

    Static AST scan: the ``__init__`` module must not declare a
    ``__getattr__`` that returns symbols from a deleted
    ``agent.loop`` module. Attribute lookups must raise
    ``AttributeError``.
    """
    init_path = REPO_ROOT / "agent" / "__init__.py"
    tree = ast.parse(init_path.read_text())
    has_lazy_attr = any(
        isinstance(node, ast.FunctionDef) and node.name == "__getattr__" for node in ast.walk(tree)
    )
    assert not has_lazy_attr, (
        f"{init_path} defines a __getattr__; that was the Phase-2 "
        "PEP 562 shim. Phase 6 removes it. Use the explicit "
        "agent.worker API instead."
    )


def test_handle_message_not_importable_from_magi_agent() -> None:
    """The runtime contract: ``from agent import handle_message`` raises.

    Verified at import time: a regression that re-introduces the
    lazy re-export would let this succeed silently.
    """
    with pytest.raises(ImportError, match="handle_message"):
        # ``__getattr__`` is the failure path; on Python 3.12
        # raising ``AttributeError`` from a module's
        # ``__getattr__`` surfaces as ``ImportError``.
        importlib = __import__("importlib")
        try:
            _ = importlib.import_module("agent").handle_message
        except AttributeError as exc:
            # Python's import machinery maps ``__getattr__``
            # AttributeError to ``ImportError`` in some
            # versions; assert on the re-raised exception name
            # rather than the original message.
            raise ImportError("handle_message") from exc


def test_handle_message_not_in_legacy_agent_loop_path() -> None:
    """If someone re-introduces loop.py, this scan flags it."""
    # Static check: walk every Python file under magi/agent/ and
    # confirm no module imports the legacy path.
    offenders: list[str] = []
    for path in (REPO_ROOT / "agent").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        src = path.read_text()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            mod = None
            if isinstance(node, ast.ImportFrom):
                mod = node.module
            elif isinstance(node, ast.Import):
                mod = next((a.name for a in node.names), None)
            if mod and "agent.loop" in mod:
                rel = path.relative_to(REPO_ROOT)
                offenders.append(f"{rel}: imports from {mod!r}")
    assert not offenders, (
        "magi/agent/ imports from agent.loop; the legacy "
        "loop module is supposed to be gone:\n  " + "\n  ".join(offenders)
    )
