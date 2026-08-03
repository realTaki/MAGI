"""AST-based import boundary enforcement.

Walks every Python file under ``magi/`` and fails if any file in
``magi/agent``, ``magi/tools``, ``magi/channels``, or ``magi/db`` imports
from a package it must not depend on, per the BUS-centric architecture
(plan: docs/MAGI_BUS_CENTRIC_ARCHITECTURE_REFACTOR_PLAN.md +
declarative-crafting-moth.md).

The rule is encoded as ``(source_package_prefix, forbidden_target_prefixes)``.
Internal subpackage references (e.g. ``magi.agent.llm`` from ``magi.agent.*``)
are allowed by filtering the forbidden prefix list against the source's own
package chain.

``magi/bus`` is the only package allowed to import from both
``magi/agent`` (no), wait — bus is FORBIDDEN from importing tools / channels /
agent implementations. The current rule for bus is "no Tool classes, no
Channel adapters, no AgentWorker, no LLM providers, no Telegram clients".

Run via::

    uv run pytest tests/architecture/test_import_boundaries.py

An empty ``allowlist`` constant (default) means zero exceptions.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
MAGI_ROOT = REPO_ROOT / "magi"

# (source_prefix, [forbidden_target_prefixes])
# A file under "magi.agent.*" must not import from "magi.db", "magi.tools",
# or "magi.channels". It MAY import from "magi.bus" and its own
# subpackages ("magi.agent.llm" etc.).
RULES: list[tuple[str, list[str]]] = [
    ("magi.agent", ["magi.db", "magi.tools", "magi.channels"]),
    ("magi.tools", ["magi.agent", "magi.db", "magi.channels"]),
    ("magi.channels", ["magi.agent", "magi.tools", "magi.db"]),
    # db is the lowest layer — must not import from anything above.
    # ``magi.bus.models`` is the lone permitted target: the plan explicitly
    # says ORM tables are bus-owned (plan §"Bus models" lists the file
    # tree at magi/bus/models/{local,magis,queue}) and ``magi/db/__init__``
    # re-exports them, ``magi/db/alembic/env.py`` imports each module so
    # ``alembic revision --autogenerate`` sees complete metadata, and
    # ``magi/db/engine.py`` calls ``Base.metadata.create_all`` via the
    # same import set. Models are passive data definitions; they have no
    # business logic to hide from the db layer. The exception is
    # enforced in ``_rule_violations`` via ``ALLOWED_BUS_SUBDOMAINS_*``.
    ("magi.db", ["magi.bus", "magi.agent", "magi.tools", "magi.channels"]),
    # bus is the application core — must not import tool/channel/agent
    # implementations, LLM providers, or Telegram clients.
    (
        "magi.bus",
        [
            "magi.tools",
            "magi.channels",
            "magi.agent.worker",
            "magi.agent.step",
            "magi.agent.llm",
        ],
    ),
]

# Allowlist of (source_file, forbidden_prefix) tuples that are explicitly
# permitted during migration. Each entry MUST be removed before the
# refactor ships. Empty at end of migration.
ALLOWLIST: set[tuple[str, str]] = set()


def _package_chain_prefixes(module_name: str) -> list[str]:
    """['magi.agent.llm.providers'] -> ['magi.agent.llm.providers', 'magi.agent.llm', 'magi.agent', 'magi']"""
    parts = module_name.split(".")
    return [".".join(parts[:i]) for i in range(len(parts), 0, -1)]


def _is_internal(source_pkg: str, target_module: str) -> bool:
    """True if ``target_module`` is a subpackage of ``source_pkg``."""
    target_chain = _package_chain_prefixes(target_module)
    return source_pkg in target_chain


def _module_name_from_path(path: Path) -> str | None:
    """Convert a .py path to a dotted module name under magi."""
    try:
        rel = path.relative_to(REPO_ROOT)
    except ValueError:
        return None
    parts = rel.with_suffix("").parts
    if not parts or parts[0] != "magi":
        return None
    return ".".join(parts)


def _iter_python_files() -> list[Path]:
    return sorted(MAGI_ROOT.rglob("*.py"))


def _imported_modules(py_path: Path) -> list[tuple[str, int]]:
    """Return [(module_name, lineno)] for every import in the file."""
    try:
        source = py_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source, filename=str(py_path))
    except SyntaxError:
        return []

    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            # ``from .x import y`` — node.module is "x"; we treat as
            # importing "x" for boundary purposes. ``from . import x``
            # gives a relative module we resolve against the file's
            # package. We only enforce boundaries for fully-qualified
            # modules anyway, so a level-1 import of "magi.bus" inside
            # magi/agent/x.py is caught by the ImportFrom with module
            # being "magi.bus" after resolution — but in practice, the
            # repo uses absolute imports throughout, so we don't need
            # to resolve relative levels.
            out.append((node.module, node.lineno))
    return out


def _rule_violations() -> list[tuple[Path, int, str, str, str]]:
    """Walk magi/ and return all boundary violations.

    Each entry: (file, lineno, source_module, target_module, forbidden_prefix)
    """
    # Sources that may import ``magi.bus.models.*`` (passive data
    # tables — see plan §"Bus models"). These are the ONLY ``magi.bus``
    # submodules that the lowest layer is allowed to touch.
    ALLOWED_BUS_SUBDOMAINS_FOR_LOWER_LAYERS: dict[str, set[str]] = {
        "magi.db": {"magi.bus.models"},
    }

    violations: list[tuple[Path, int, str, str, str]] = []
    for py_path in _iter_python_files():
        source_module = _module_name_from_path(py_path)
        if source_module is None:
            continue
        for source_prefix, forbidden_prefixes in RULES:
            if not _is_internal(source_prefix, source_module):
                continue
            allowed_bus_subs = ALLOWED_BUS_SUBDOMAINS_FOR_LOWER_LAYERS.get(source_prefix, set())
            for target, lineno in _imported_modules(py_path):
                for forbidden in forbidden_prefixes:
                    if not _is_internal(forbidden, target):
                        continue
                    if allowed_bus_subs and any(_is_internal(allowed, target) for allowed in allowed_bus_subs):
                        continue
                    key = (str(py_path.relative_to(REPO_ROOT)), forbidden)
                    if key in ALLOWLIST:
                        continue
                    violations.append(
                        (
                            py_path,
                            lineno,
                            source_module,
                            target,
                            forbidden,
                        )
                    )
    return violations


def test_import_boundaries_clean() -> None:
    """No forbidden cross-package imports exist anywhere in magi/.

    The test is intentionally a single fat assertion so the failure
    message lists every violation at once — easier to triage than 114
    individual test failures.
    """
    violations = _rule_violations()
    if not violations:
        return
    lines = ["Forbidden cross-package imports found:"]
    for py_path, lineno, source, target, forbidden in violations:
        rel = py_path.relative_to(REPO_ROOT)
        lines.append(
            f"  {rel}:{lineno}  {source}  ->  {target}    "
            f"(forbidden: {forbidden})"
        )
    lines.append("")
    lines.append(
        f"Total violations: {len(violations)}.  "
        "Each must be removed by routing through magi.bus.* or "
        "moved out of the source package.  See plan in "
        "/root/.claude/plans/declarative-crafting-moth.md."
    )
    pytest.fail("\n".join(lines))


def test_allowlist_is_empty() -> None:
    """Migration must end with an empty allowlist.

    Allows tracking during the refactor — set ALLOWLIST entries to
    permit a temporary cross-package call while its real replacement
    is being built. Each entry should be removed at the end.
    """
    if not ALLOWLIST:
        return
    lines = [
        f"tests/architecture/test_import_boundaries.py has "
        f"{len(ALLOWLIST)} allowlist entries — must be empty at end of migration:",
    ]
    for path, prefix in sorted(ALLOWLIST):
        lines.append(f"  ({path!r}, {prefix!r})")
    pytest.fail("\n".join(lines))
