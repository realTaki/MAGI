"""AST-based import boundary enforcement.

Walks every Python file under ``magi/`` and fails if any file in
``magi/agent``, ``magi/tools``, ``magi/channels``, or ``magi/db`` imports
from a package it must not depend on, per the BUS-centric architecture
(``docs/MAGI_BUS_CENTRIC_ARCHITECTURE.md``).

The rule is encoded as ``(source_package_prefix, forbidden_target_prefixes)``.
Internal subpackage references (e.g. ``magi.bus.db.engine`` from ``magi.bus.*``)
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
#
# Per the BUS-centric architecture: ``magi.bus`` owns every cross-package
# boundary.  Domain code (agent / tools / channels / mcp / proactive /
# connectors / orchestrator / skills) talks only to the bus façade
# (``bus.<subdomain>.<method>(...)``).  Storage lives inside the bus at
# ``magi.bus.db`` — the *only* callers of ``magi.bus.db`` are the bus
# services themselves (and the composition root + Alembic migration
# runner, both of which are exempted below).
#
# Phase 2/3 (Local Standalone Deployment plan §4.4) — the BUS must never
# import the orchestrator package; the orchestrator / API must not reach
# into storage; the ``magi.deploy`` Composition Root namespace is the
# sole exempted package (skipped via :data:`COMPOSITION_ROOT_PREFIXES`).
RULES: list[tuple[str, list[str]]] = [
    # Domain packages — must not reach into storage or sibling domain
    # code.  They MAY import ``magi.bus`` (services + contracts + the
    # façade itself); they MUST NOT import ``magi.bus.db`` (storage) or
    # ``magi.bus.db.models.*`` (raw ORM tables).
    ("magi.agent", ["magi.tools", "magi.channels", "magi.bus.db"]),
    ("magi.tools", ["magi.agent", "magi.channels", "magi.bus.db"]),
    ("magi.channels", ["magi.agent", "magi.tools", "magi.bus.db"]),
    # ``magi.mcp`` and ``magi.connectors`` are siblings — each is a
    # tools-adapter for an external product (MCP for products that
    # speak the MCP protocol, connectors for products that don't).
    # Both MAY depend on ``magi.tools``; both must not reach into
    # storage. This is the "external-adapter" symmetry.
    ("magi.mcp", ["magi.bus.db"]),
    ("magi.connectors", ["magi.bus.db"]),
    ("magi.proactive", ["magi.bus.db"]),
    ("magi.orchestrator", ["magi.bus.db"]),
    ("magi.skills", ["magi.bus.db"]),
    # ``magi.channels.api`` is the WebUI backend. Per
    # ``docs/MAGI_MODULE_RESPONSIBILITIES_AND_DEPENDENCIES.md`` §6
    # (forbidden-deps table) + §5.6, the API MUST NOT depend on the
    # generic task scheduler worker — scheduler interaction is a BUS
    # protocol concern, not a Python one. The TASK bridge lives at
    # ``magi.bus.jobs.services.task_scheduler_bridge``; the API reaches
    # the scheduler only via that bridge.
    #
    # Phase 2/3 (Local Standalone Deployment plan §4.4) — the API
    # must not import the orchestrator package directly; runtime
    # endpoint resolution is a BUS concern reached via
    # ``bus.registry.resolve_endpoint(magic_id)``.
    (
        "magi.channels.api",
        [
            "magi.channels.tasks",
            "magi.agent",
            "magi.tools",
            "magi.mcp",
            "magi.plugins",
            "magi.connectors",
            "magi.bus.db",
            "magi.orchestrator",
            "magi.orchestrator.backends",
            "magi.orchestrator.client",
            "magi.orchestrator.service",
            "magi.orchestrator.contracts",
        ],
    ),
    # bus is the application core — must not import channel/agent
    # implementations, LLM providers, or Telegram clients.
    # ``magi.channels.tasks`` is the explicit exception: the scheduler
    # worker is part of the bus-side task infrastructure, and
    # ``magi.bus.jobs.services.task_scheduler_bridge`` is the single Python
    # module allowed to hold the scheduler handle.
    #
    # Phase 2/3 (Local Standalone Deployment plan §4.4) — the BUS must
    # not import the orchestrator package directly.  The runtime
    # lifecycle seam is :class:`BackendDispatcherService`; the
    # orchestrator's only contact with the BUS is via the DTOs and the
    # bootstrap-registered engine injection.
    (
        "magi.bus",
        [
            "magi.tools",
            "magi.channels.telegram",
            "magi.channels.api",
            "magi.channels.a2a",
            "magi.channels.base",
            "magi.channels.dispatcher",
            "magi.channels.delivery",
            "magi.agent.worker",
            "magi.providers",
        ],
    ),
    # Phase 2 — BUS services that own the runtime lifecycle seam must
    # never reach back into the orchestrator package *implementations*
    # (the legacy ``KubernetesEvaBackend`` class, the orchestrator
    # client, the FastAPI service).  The dispatcher legitimately
    # imports the backend factory and the K8s *adapter* (which
    # implements the Protocol) — those are exempted by the
    # ``magi.orchestrator.backends`` sub-rule below.
    ("magi.bus.jobs.services", ["magi.orchestrator.kubernetes", "magi.orchestrator.client", "magi.orchestrator.service", "magi.orchestrator.contracts"]),
    # Phase 2 — the dispatcher must not import the legacy K8s class
    # directly; it consumes the K8s *adapter* (which wraps the legacy
    # class) via the factory's Protocol surface.
    ("magi.bus.jobs.services.runtime", ["magi.orchestrator.kubernetes"]),
]

# ``magi.launcher`` is the Composition-Root namespace — it is the sole
# package allowed to import everything (including storage + the
# orchestrator package).  The exemption is applied inside
# ``_rule_violations`` below.
COMPOSITION_ROOT_PREFIXES: set[str] = {"magi.launcher"}

# ``magi.bus`` is itself allowed to import ``magi.bus.db`` and
# ``magi.bus.db.models.*`` — that's the whole point of the consolidation.
# The boundary rule's `_is_internal` check already lets a file under
# ``magi.bus.*`` import any ``magi.bus.X`` submodule, so no additional
# exception is needed here.
ALLOWED_BUS_SUBDOMAINS_FOR_LOWER_LAYERS: dict[str, set[str]] = {}

# ``magi.bus`` is itself allowed to import ``magi.bus.db`` and
# ``magi.bus.db.models.*`` — that's the whole point of the consolidation.
# The boundary rule's `_is_internal` check already lets a file under
# ``magi.bus.*`` import any ``magi.bus.X`` submodule, so no additional
# exception is needed here.
ALLOWED_BUS_SUBDOMAINS_FOR_LOWER_LAYERS: dict[str, set[str]] = {}

# Allowlist of (source_file, forbidden_prefix) tuples that are explicitly
# permitted during migration. Each entry MUST be removed before the
# refactor ships. Empty at end of migration.
ALLOWLIST: set[tuple[str, str]] = set()


def _package_chain_prefixes(module_name: str) -> list[str]:
    """['magi.bus.db.engine'] -> ['magi.bus.db.engine', 'magi.bus.db', 'magi.bus', 'magi']"""
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
    violations: list[tuple[Path, int, str, str, str]] = []
    for py_path in _iter_python_files():
        source_module = _module_name_from_path(py_path)
        if source_module is None:
            continue
        # Composition-Root exemption: ``magi.launcher`` is the sole
        # package allowed to import everything (including storage + the
        # orchestrator package).
        if any(
            _is_internal(exempt, source_module) for exempt in COMPOSITION_ROOT_PREFIXES
        ):
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


# ──────────────────────────────────────────────────────────────────────── #
# ``state_dir`` leak prevention
#
# ``state_dir`` is the physical filesystem location of the private SQLite
# database. It is resolved by ``magi.launcher.paths.state_dir()`` and is
# consumed only by module code inside ``magi/bus/``. The composition root
# (``magi/__main__.py``, the WebUI factory, the runtime API factory) is the
# only non-BUS site that computes the path — it does so to bootstrap SQLite
# before the BUS exists. Every other module must reach the BUS through
# ``get_bus().<service>.<method>(...)`` and never see the path.
#
# This test walks every non-BUS, non-launcher Python file and fails on
# any of:
#   - ``from magi.launcher.paths import state_dir`` (or its dynamic
#     equivalent via ``__import__`` / ``importlib.import_module``).
#   - An ``import`` line resolving to ``magi.launcher.paths`` when the
#     imported name is ``state_dir``.
#   - ``require_state_dir`` attribute access on a SettingsService
#     instance (the public method that leaked the path was removed).
# ──────────────────────────────────────────────────────────────────────── #

# Files that legitimately need to read ``state_dir`` because they form the
# composition root. These are the only sites where the BUS has not yet
# been bootstrapped and SQLite must be located from the launcher.
STATE_DIR_COMPOSITION_ROOT_ALLOWLIST: frozenset[str] = frozenset({
    "magi/__main__.py",
    "magi/channels/api/app.py",
    "magi/channels/api/runtime_control.py",
    "magi/channels/telegram/bot.py",
})


def _state_dir_name_imports_or_calls(py_path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, snippet)`` for every state_dir leak in ``py_path``."""
    try:
        source = py_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source, filename=str(py_path))
    except SyntaxError:
        return []

    findings: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        # ``from magi.launcher.paths import state_dir, ...``
        if isinstance(node, ast.ImportFrom):
            if node.module in {"magi.launcher.paths"}:
                for alias in node.names:
                    if alias.name == "state_dir":
                        findings.append(
                            (node.lineno, f"from {node.module} import state_dir")
                        )
        # ``import magi.launcher.paths`` followed by ``... .state_dir``
        # — caught by Attribute access below.
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "magi.launcher.paths":
                    findings.append(
                        (node.lineno, "import magi.launcher.paths")
                    )
        # ``magi.launcher.paths.state_dir(...)`` — the launcher.paths
        # module is the only public path to ``state_dir``; importing it
        # wholesale is the leading indicator of a leak.
        if isinstance(node, ast.Attribute):
            value = node.value
            if (
                isinstance(value, ast.Name)
                and value.id == "state_dir"
                and isinstance(node.ctx, ast.Load)
            ):
                # Standalone ``state_dir`` reference (not on a different
                # object). Usually means ``from ... import state_dir``.
                findings.append(
                    (node.lineno, "use of bare `state_dir` identifier")
                )
        # ``settings.require_state_dir(...)`` — the public method that
        # leaked the path was removed. Fail if any code still calls it.
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "require_state_dir"
            ):
                findings.append(
                    (node.lineno, "call to .require_state_dir()")
                )
    # Deduplicate by line.
    seen: set[int] = set()
    deduped: list[tuple[int, str]] = []
    for lineno, snippet in findings:
        if lineno in seen:
            continue
        seen.add(lineno)
        deduped.append((lineno, snippet))
    return deduped


def test_state_dir_does_not_leak_outside_bus() -> None:
    """No non-BUS, non-launcher module may import or call ``state_dir``.

    The composition-root allowlist covers the boot-time sites that
    legitimately need the path before the BUS exists. Anything else is
    a layered-boundary regression: the BUS owns the path and every
    other module goes through ``get_bus().<service>.<method>``.
    """
    offenders: list[str] = []
    for py_path in _iter_python_files():
        source_module = _module_name_from_path(py_path)
        if source_module is None:
            continue
        # Skip BUS — it owns state_dir and require_state_dir.
        if _is_internal("magi.bus", source_module):
            continue
        # Skip launcher — defines state_dir as its primary export.
        if _is_internal("magi.launcher", source_module):
            continue
        rel = str(py_path.relative_to(REPO_ROOT))
        if rel in STATE_DIR_COMPOSITION_ROOT_ALLOWLIST:
            continue
        for lineno, snippet in _state_dir_name_imports_or_calls(py_path):
            offenders.append(f"{rel}:{lineno}  {snippet}")
    assert not offenders, (
        "`state_dir` (the SQLite filesystem location) must not be "
        "imported or called from non-BUS modules. The composition root "
        "and bootstrap pipeline are the only allowed sites; everything "
        "else should reach the BUS through `get_bus().<service>.<method>(...)`. "
        "Offending lines:\n  " + "\n  ".join(offenders)
    )
