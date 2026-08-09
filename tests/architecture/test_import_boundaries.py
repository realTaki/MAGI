"""Bus-only import boundary enforcement."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
MAGI_ROOT = REPO_ROOT / "magi"


def _imports(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((item.name, node.lineno) for item in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.module, node.lineno))
    return found


def _production_modules() -> list[Path]:
    return [path for path in MAGI_ROOT.rglob("*.py") if "__pycache__" not in path.parts]


def test_retired_bus_names_are_not_used() -> None:
    """The single BUS implementation has no compatibility import surface."""
    retired = (
        "magi." + "new" + "_bus",
        "New" + "Bus",
        "bootstrap_" + "new" + "_bus",
    )
    offenders: list[str] = []
    for path in _production_modules():
        text = path.read_text(encoding="utf-8")
        for name in retired:
            if name in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)} -> {name}")
    assert not offenders, "retired BUS package names remain:\n  " + "\n  ".join(offenders)


def test_domain_modules_do_not_reach_into_bus_storage() -> None:
    """Domain code uses Bus Books/boards, never its ORM or engine layer."""
    domains = ("agent", "channels", "tools", "mcp", "proactive", "connectors")
    offenders: list[str] = []
    for domain in domains:
        for path in (MAGI_ROOT / domain).rglob("*.py"):
            for module, lineno in _imports(path):
                if module.startswith("magi.bus.db"):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno} -> {module}")
    assert not offenders, "domain modules must use Bus facade, not storage:\n  " + "\n  ".join(offenders)


def test_bus_does_not_import_domain_implementations() -> None:
    forbidden = ("magi.agent", "magi.channels", "magi.tools", "magi.providers")
    offenders: list[str] = []
    for path in (MAGI_ROOT / "bus").rglob("*.py"):
        for module, lineno in _imports(path):
            if any(module == root or module.startswith(root + ".") for root in forbidden):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno} -> {module}")
    assert not offenders, "Bus must not import domain implementations:\n  " + "\n  ".join(offenders)
