"""Bus-only import boundary enforcement."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAGI_ROOT = REPO_ROOT


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


def test_vnext_bus_is_a_first_class_package() -> None:
    """``bus`` is the MAGI-BUS vNext package, not a compatibility shim."""
    vnext_root = MAGI_ROOT / "bus"
    assert (vnext_root / "__init__.py").is_file()
    assert (vnext_root / "bus.py").is_file()
    assert (vnext_root / "firmware" / "__init__.py").is_file()


def test_domain_modules_do_not_reach_into_bus_storage() -> None:
    """Domain code uses Bus Books/boards, never its ORM or engine layer."""
    domains = ("agent", "channels", "tools", "mcp", "proactive", "connectors")
    offenders: list[str] = []
    for domain in domains:
        for path in (MAGI_ROOT / domain).rglob("*.py"):
            for module, lineno in _imports(path):
                if module.startswith("bus.bases.db"):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno} -> {module}")
    assert not offenders, "domain modules must use Bus facade, not storage:\n  " + "\n  ".join(
        offenders
    )


def test_retired_bus_package_names_are_not_imported() -> None:
    """``guild`` / ``library`` / top-level ``bus.db`` have no import surface."""
    retired = ("bus.db", "bus.guild", "bus.library")
    offenders: list[str] = []
    for path in _production_modules():
        for module, lineno in _imports(path):
            if any(module == root or module.startswith(root + ".") for root in retired):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno} -> {module}")
    assert not offenders, "retired BUS package names remain:\n  " + "\n  ".join(offenders)


def test_bases_do_not_import_firmwares() -> None:
    """Bases own contracts and storage engines, never firmware tables.

    Table/column definitions, Alembic revisions, and schema
    synchronisation live in ``bus.firmwares``. Bases must stay
    firmware-free so the integration layer does not encode business data.
    """
    offenders: list[str] = []
    for path in (MAGI_ROOT / "bus" / "bases").rglob("*.py"):
        for module, lineno in _imports(path):
            if module == "bus.firmwares" or module.startswith("bus.firmwares."):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno} -> {module}")
    assert not offenders, "bases must not import firmwares:\n  " + "\n  ".join(offenders)


def test_bus_does_not_import_domain_implementations() -> None:
    forbidden = ("agent", "channels", "tools", "providers")
    offenders: list[str] = []
    for path in (MAGI_ROOT / "bus").rglob("*.py"):
        for module, lineno in _imports(path):
            if any(module == root or module.startswith(root + ".") for root in forbidden):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno} -> {module}")
    assert not offenders, "Bus must not import domain implementations:\n  " + "\n  ".join(offenders)


def test_bus_does_not_depend_on_magi_service() -> None:
    """The composition root (``magi``) imports the bus, never
    the other way around.  Catches the legacy reverse edge where
    :mod:`bus.firmwares.books.file.skillsBook` reached into
    the service layer.

    Note: ``magi`` itself is a composition root and is
    *expected* to import from the bus; the test only walks the
    bus subtree, not the service subtree.
    """
    offenders: list[str] = []
    for path in (MAGI_ROOT / "bus").rglob("*.py"):
        for module, lineno in _imports(path):
            if module == "magi" or module.startswith("magi."):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno} -> {module}")
    assert not offenders, (
        "Bus must not import from the composition root "
        "(magi); the bus layer should reach for its own "
        "resource resolvers:\n  " + "\n  ".join(offenders)
    )


def test_api_does_not_import_service_constructors() -> None:
    """The API accepts injected BUS state; it does not own service startup."""
    forbidden = ("magi.service", "magi.__main__")
    offenders: list[str] = []
    for path in (MAGI_ROOT / "magi" / "api").rglob("*.py"):
        for module, lineno in _imports(path):
            if any(module == root or module.startswith(root + ".") for root in forbidden):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno} -> {module}")
    assert not offenders, (
        "magi.api must not reach back into service constructors; "
        "inject the constructed BUS instead:\n  " + "\n  ".join(offenders)
    )
