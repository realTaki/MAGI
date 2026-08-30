"""Static package-boundary guard.

Locks the design §18 rule: agent/tools must not import from
``channels.api.*`` (the channels-specific HTTP surface).
BUS owns persistence behind ``bus.bases.db``. A future change that
re-introduces a reverse domain import must fail here before it reaches
production.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Modules that are allowed to import from channels.api.
# Today only the test suite and the api layer itself.
EXEMPT_PREFIXES: tuple[str, ...] = (
    "channels/api/",
    "tests/",
)

# Modules we are enforcing the rule on.
SCAN_PREFIXES: tuple[str, ...] = (
    "agent/",
    "tools/",
    "proactive/",
)

# These are the remaining production Actor/Tool entry paths that must retain
# their BUS-only boundary. Delivery is now represented by the durable
# ``deliveryNotifyJob`` board and no longer has a channels/delivery.py module.
BUS_ONLY_PATHS: tuple[str, ...] = (
    "agent/worker.py",
    "agent/agent_context.py",
    "tools/BaseTool.py",
    "tools/worker.py",
)

_FORBIDDEN_BY_PATH: dict[str, tuple[str, ...]] = {
    "agent/": ("bus.bases.db", "tools", "channels"),
    "tools/": ("bus.bases.db", "agent", "channels"),
    "channels/": ("bus.bases.db", "agent", "tools"),
}


def _collect_imports(py_path: Path) -> list[tuple[str, int]]:
    """Return ``(module, lineno)`` for every ``ImportFrom`` / ``Import`` in a file."""
    out: list[tuple[str, int]] = []
    try:
        tree = ast.parse(py_path.read_text(encoding="utf-8"))
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("channels.api"):
                out.append((node.module, node.lineno))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("channels.api"):
                    out.append((alias.name, node.lineno))
    return out


def _collect_forbidden_imports(py_path: Path, forbidden: tuple[str, ...]) -> list[tuple[str, int]]:
    """Find static and known dynamic imports of forbidden package roots."""
    tree = ast.parse(py_path.read_text(encoding="utf-8"))
    offenders: list[tuple[str, int]] = []

    def matches(module: str) -> bool:
        return any(module == root or module.startswith(root + ".") for root in forbidden)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders.extend(
                (alias.name, node.lineno) for alias in node.names if matches(alias.name)
            )
        elif isinstance(node, ast.ImportFrom):
            if node.module and matches(node.module):
                offenders.append((node.module, node.lineno))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "__import__"
        ):
            if (
                node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                if matches(node.args[0].value):
                    offenders.append((node.args[0].value, node.lineno))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "import_module"
        ):
            if (
                node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                if matches(node.args[0].value):
                    offenders.append((node.args[0].value, node.lineno))
    return offenders


def test_migrated_actor_tool_delivery_paths_only_depend_on_bus() -> None:
    """Enforce the BUS boundary on the migrated durable execution path."""
    offenders: list[str] = []
    for relative in BUS_ONLY_PATHS:
        path = REPO_ROOT / relative
        forbidden = next(
            roots for prefix, roots in _FORBIDDEN_BY_PATH.items() if relative.startswith(prefix)
        )
        for module, lineno in _collect_forbidden_imports(path, forbidden):
            offenders.append(f"{relative}:{lineno} imports {module!r}")
    assert not offenders, "Runtime paths must cross domains through BUS:\n  " + "\n  ".join(
        offenders
    )


def test_agent_module_does_not_import_api() -> None:
    offenders: list[str] = []
    for prefix in ("agent/",):
        for path in (REPO_ROOT / prefix).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            for module, lineno in _collect_imports(path):
                rel = path.relative_to(REPO_ROOT)
                offenders.append(f"{rel}:{lineno}  imports  {module!r}")
    assert not offenders, (
        "agent/ imports from channels.api.* — this "
        "violates design §18. Move the helper to a neutral module "
        "(a neutral BUS contract or agent helper):\n  " + "\n  ".join(offenders)
    )


def test_tools_module_does_not_import_api() -> None:
    offenders: list[str] = []
    for prefix in ("tools/",):
        for path in (REPO_ROOT / prefix).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            for module, lineno in _collect_imports(path):
                rel = path.relative_to(REPO_ROOT)
                offenders.append(f"{rel}:{lineno}  imports  {module!r}")
    assert not offenders, (
        "tools/ imports from channels.api.* — this "
        "violates design §18. Move the helper to a neutral module "
        "(a neutral BUS contract or agent helper):\n  " + "\n  ".join(offenders)
    )


def test_proactive_module_does_not_import_api() -> None:
    """Same rule for the proactive subsystem (future workers will live here)."""
    offenders: list[str] = []
    for path in (REPO_ROOT / "proactive/").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for module, lineno in _collect_imports(path):
            rel = path.relative_to(REPO_ROOT)
            offenders.append(f"{rel}:{lineno}  imports  {module!r}")
    assert not offenders, (
        "proactive/ imports from channels.api.* — this "
        "violates design §18:\n  " + "\n  ".join(offenders)
    )
