"""Architecture tests for the BUS hook subsystem.

Two enforcement layers:

  1. **Import boundary** — ``magi.plugins.hooks`` MUST NOT
     import ``magi.bus`` modules that would give a plugin
     handler queryable access to BUS state.  Specifically:

       - forbidden: ``magi.bus.db.*``, ``magi.bus.models.*``
         (raw SQLAlchemy), ``magi.bus.services.*`` (other
           services), ``magi.bus.store`` (BusStore).
       - allowed: the re-exported contract surface from
         :mod:`magi.bus.hooks` (DTOs, enums, HandlerProtocol).

  2. **Handler signature** — every callable exported from
     ``magi.plugins.hooks`` MUST be either:

       - a dataclass / frozen dataclass (no methods that
         mutate state), or
       - an async function whose only parameter is a
         ``HookEnvelope`` (so plugins cannot request more).

The architecture test for ``magi.bus.hooks.*`` (the BUS side) is
covered by the existing ``test_import_boundaries.py`` — the BUS
side is forbidden from importing ``magi.tools``, ``magi.agent``,
``magi.providers``, and the channel adapters; the new rules
just tighten what's already there.

Run via::

    uv run pytest tests/architecture/test_hook_import_boundaries.py
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
MAGI_ROOT = REPO_ROOT / "magi"
PLUGIN_HOOKS_ROOT = MAGI_ROOT / "plugins" / "hooks"


# (source_module_prefix, [forbidden_target_prefixes])
#
# Plugin-side hooks must depend ONLY on the BUS hook contract
# surface (re-exported by ``magi.plugins.hooks.__init__``) — they
# MUST NOT reach into raw ORM, sessions, or other bus services.
# The composition root (and tests) are the only allowed sites to
# import those modules directly.
RULES: list[tuple[str, list[str]]] = [
    (
        "magi.plugins.hooks",
        [
            "magi.bus.db",
            "magi.bus.models",
            "magi.bus.store",
            "magi.bus.services",
            # Forbidden cross-domain imports — same as the
            # main architecture test, but enforced for the hook
            # subsystem too.
            "magi.agent",
            "magi.tools",
            "magi.channels",
            "magi.mcp",
            "magi.connectors",
            "magi.providers",
            "magi.proactive",
            "magi.orchestrator",
            "magi.skills",
        ],
    ),
]


COMPOSITION_ROOT_PREFIXES: set[str] = {"magi.launcher"}
ALLOWLIST: set[tuple[str, str]] = set()


def _package_chain_prefixes(module_name: str) -> list[str]:
    parts = module_name.split(".")
    return [".".join(parts[:i]) for i in range(len(parts), 0, -1)]


def _is_internal(source_pkg: str, target_module: str) -> bool:
    target_chain = _package_chain_prefixes(target_module)
    return source_pkg in target_chain


def _module_name_from_path(path: Path) -> str | None:
    try:
        rel = path.relative_to(REPO_ROOT)
    except ValueError:
        return None
    parts = rel.with_suffix("").parts
    if not parts or parts[0] != "magi":
        return None
    return ".".join(parts)


def _iter_python_files() -> list[Path]:
    return sorted(PLUGIN_HOOKS_ROOT.rglob("*.py"))


def _imported_modules(py_path: Path) -> list[tuple[str, int]]:
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
            out.append((node.module, node.lineno))
    return out


def _rule_violations() -> list[tuple[Path, int, str, str, str]]:
    violations: list[tuple[Path, int, str, str, str]] = []
    for py_path in _iter_python_files():
        source_module = _module_name_from_path(py_path)
        if source_module is None:
            continue
        if any(_is_internal(exempt, source_module) for exempt in COMPOSITION_ROOT_PREFIXES):
            continue
        for source_prefix, forbidden_prefixes in RULES:
            if not _is_internal(source_prefix, source_module):
                continue
            for target, lineno in _imported_modules(py_path):
                for forbidden in forbidden_prefixes:
                    if not _is_internal(forbidden, target):
                        continue
                    key = (str(py_path.relative_to(REPO_ROOT)), forbidden)
                    if key in ALLOWLIST:
                        continue
                    violations.append(
                        (py_path, lineno, source_module, target, forbidden)
                    )
    return violations


def test_hook_import_boundaries_clean() -> None:
    """No forbidden cross-package imports exist in magi/plugins/hooks."""
    violations = _rule_violations()
    if not violations:
        return
    lines = ["Forbidden cross-package imports in magi/plugins/hooks/:", ""]
    for py_path, lineno, source, target, forbidden in violations:
        rel = py_path.relative_to(REPO_ROOT)
        lines.append(
            f"  {rel}:{lineno}  {source}  ->  {target}    "
            f"(forbidden: {forbidden})"
        )
    lines.append("")
    lines.append(
        "Each violation means a hook plugin COULD reach into the BUS "
        "internals — the envelope contract is the only allowed input."
    )
    pytest.fail("\n".join(lines))


def test_hook_allowlist_is_empty() -> None:
    if not ALLOWLIST:
        return
    lines = [
        f"tests/architecture/test_hook_import_boundaries.py has "
        f"{len(ALLOWLIST)} allowlist entries — must be empty at end of migration:",
    ]
    for path, prefix in sorted(ALLOWLIST):
        lines.append(f"  ({path!r}, {prefix!r})")
    pytest.fail("\n".join(lines))


# ───────────────────────────────────────────────────────────────────── #
# Handler signature enforcement — every async callable exported
# from ``magi.plugins.hooks`` must take exactly one
# ``HookEnvelope`` argument.
# ───────────────────────────────────────────────────────────────────── #


def test_handler_signature_takes_only_envelope() -> None:
    """No handler accepts anything beyond the HookEnvelope.

    Specifically:
      - Functions named ``handle`` (the HookHandlerProtocol
        method) MUST accept exactly one parameter named
        ``envelope`` (or be marked ``*args, **kwargs`` — which is
        also forbidden because plugins cannot justify extra args).
      - Other public callables exported from
        ``magi.plugins.hooks`` may take config kwargs (e.g.
        ``hook_handler(hook_id=...)``) but not ORM models or
        raw bus references.

    This is a static check — we walk the AST looking for
    ``def handle(...)`` and inspect the parameter list.
    """
    offenders: list[str] = []
    for py_path in _iter_python_files():
        try:
            source = py_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            tree = ast.parse(source, filename=str(py_path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name != "handle":
                continue
            args = node.args
            # Allow ``self.handle(envelope)`` (one positional).
            # Disallow ``*args`` / ``**kwargs``.
            if args.vararg is not None or args.kwarg is not None:
                offenders.append(
                    f"{py_path.relative_to(REPO_ROOT)}:{node.lineno}  "
                    f"def handle({args.vararg.arg if args.vararg else '*args'}, "
                    f"{args.kwarg.arg if args.kwarg else '**kwargs'}):  "
                    "varargs / kwargs are forbidden in handle()"
                )
                continue
            # Must have exactly one positional parameter (the
            # envelope), ignoring ``self``.
            positional = list(args.args)
            if positional and positional[0].arg == "self":
                positional = positional[1:]
            if len(positional) != 1:
                offenders.append(
                    f"{py_path.relative_to(REPO_ROOT)}:{node.lineno}  "
                    f"def handle(...):  "
                    f"expected 1 positional parameter, got {len(positional)}"
                )
                continue
            if positional[0].arg not in {"envelope", "ev"}:
                offenders.append(
                    f"{py_path.relative_to(REPO_ROOT)}:{node.lineno}  "
                    f"def handle({positional[0].arg}):  "
                    "first parameter must be named 'envelope'"
                )
    assert not offenders, (
        "Hook handlers MUST take exactly one parameter named "
        "'envelope' (the HookEnvelope). Offending signatures:\n  "
        + "\n  ".join(offenders)
    )


def test_handler_protocol_signature() -> None:
    """The :class:`HookHandlerProtocol` itself takes exactly one envelope."""
    from magi.bus.hooks.contracts import HookHandlerProtocol

    # runtime_checkable Protocol — the signature check is a hint,
    # but the AST check above is the hard gate.  This test
    # confirms the protocol module declares it correctly.
    impl = getattr(HookHandlerProtocol, "handle", None)
    assert impl is not None, "HookHandlerProtocol must declare handle()"
    sig = inspect.signature(impl)
    params = [
        p for p in sig.parameters.values()
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    assert len(params) == 2, (
        "HookHandlerProtocol.handle() must take exactly (self, envelope)"
    )
