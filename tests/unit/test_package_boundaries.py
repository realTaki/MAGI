"""Static package-boundary guard.

Locks the design §18 rule: agent/tools must not import from
``magi.channels.webui.api.*`` (the channels-specific HTTP surface).
The P1.1 refactor moved read helpers to ``magi.db.runtime_settings``
and search to ``magi.agent.memory.session.search`` precisely to
break this cycle. A future change that re-introduces the reverse
import would crash here so the regression is caught at CI time,
not in production.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Modules that are allowed to import from magi.channels.webui.api.
# Today only the test suite and the webui api layer itself.
EXEMPT_PREFIXES: tuple[str, ...] = (
    "magi/channels/webui/",
    "tests/",
)

# Modules we are enforcing the rule on.
SCAN_PREFIXES: tuple[str, ...] = (
    "magi/agent/",
    "magi/tools/",
    "magi/proactive/",
)


def _collect_imports(py_path: Path) -> list[tuple[str, int]]:
    """Return ``(module, lineno)`` for every ``ImportFrom`` / ``Import`` in a file."""
    out: list[tuple[str, int]] = []
    try:
        tree = ast.parse(py_path.read_text(encoding="utf-8"))
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("magi.channels.webui.api"):
                out.append((node.module, node.lineno))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("magi.channels.webui.api"):
                    out.append((alias.name, node.lineno))
    return out


def test_agent_module_does_not_import_webui_api() -> None:
    offenders: list[str] = []
    for prefix in ("magi/agent/",):
        for path in (REPO_ROOT / prefix).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            for module, lineno in _collect_imports(path):
                rel = path.relative_to(REPO_ROOT)
                offenders.append(f"{rel}:{lineno}  imports  {module!r}")
    assert not offenders, (
        "magi/agent/ imports from magi.channels.webui.api.* — this "
        "violates design §18. Move the helper to a neutral module "
        "(magi.db.runtime_settings or magi.agent.memory.session.search):\n  "
        + "\n  ".join(offenders)
    )


def test_tools_module_does_not_import_webui_api() -> None:
    offenders: list[str] = []
    for prefix in ("magi/tools/",):
        for path in (REPO_ROOT / prefix).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            for module, lineno in _collect_imports(path):
                rel = path.relative_to(REPO_ROOT)
                offenders.append(f"{rel}:{lineno}  imports  {module!r}")
    assert not offenders, (
        "magi/tools/ imports from magi.channels.webui.api.* — this "
        "violates design §18. Move the helper to a neutral module "
        "(magi.db.runtime_settings or magi.agent.memory.session.search):\n  "
        + "\n  ".join(offenders)
    )


def test_proactive_module_does_not_import_webui_api() -> None:
    """Same rule for the proactive subsystem (future workers will live here)."""
    offenders: list[str] = []
    for path in (REPO_ROOT / "magi/proactive/").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for module, lineno in _collect_imports(path):
            rel = path.relative_to(REPO_ROOT)
            offenders.append(f"{rel}:{lineno}  imports  {module!r}")
    assert not offenders, (
        "magi/proactive/ imports from magi.channels.webui.api.* — this "
        "violates design §18:\n  " + "\n  ".join(offenders)
    )