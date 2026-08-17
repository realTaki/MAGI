"""Bus-only import boundary enforcement."""

from __future__ import annotations

import ast
from pathlib import Path

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
                if module.startswith("magi.bus.bases.db"):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno} -> {module}")
    assert not offenders, "domain modules must use Bus facade, not storage:\n  " + "\n  ".join(
        offenders
    )


def test_retired_bus_package_names_are_not_imported() -> None:
    """``guild`` / ``library`` / top-level ``bus.db`` have no import surface."""
    retired = ("magi.bus.db", "magi.bus.guild", "magi.bus.library")
    offenders: list[str] = []
    for path in _production_modules():
        for module, lineno in _imports(path):
            if any(module == root or module.startswith(root + ".") for root in retired):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno} -> {module}")
    assert not offenders, "retired BUS package names remain:\n  " + "\n  ".join(offenders)


def test_bases_do_not_import_firmwares() -> None:
    """Bases own contracts and storage; only schema registration may look up.

    ``bases.db.schema`` and the Alembic env import ``magi.bus.firmwares``
    so ``Base.metadata`` is populated before create_all / upgrade. Every
    other bases module must stay firmware-free.
    """
    allowed = {
        MAGI_ROOT / "bus" / "bases" / "db" / "schema.py",
        MAGI_ROOT / "bus" / "bases" / "db" / "alembic" / "env.py",
    }
    offenders: list[str] = []
    for path in (MAGI_ROOT / "bus" / "bases").rglob("*.py"):
        if path in allowed:
            continue
        for module, lineno in _imports(path):
            if module == "magi.bus.firmwares" or module.startswith("magi.bus.firmwares."):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno} -> {module}")
    assert not offenders, "bases must not import firmwares:\n  " + "\n  ".join(offenders)


def test_bus_does_not_import_domain_implementations() -> None:
    forbidden = ("magi.agent", "magi.channels", "magi.tools", "magi.providers")
    offenders: list[str] = []
    for path in (MAGI_ROOT / "bus").rglob("*.py"):
        for module, lineno in _imports(path):
            if any(module == root or module.startswith(root + ".") for root in forbidden):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno} -> {module}")
    assert not offenders, "Bus must not import domain implementations:\n  " + "\n  ".join(offenders)


def test_bus_does_not_depend_on_startup() -> None:
    """The composition root (``magi.startup``) imports the bus, never
    the other way around.  Catches the legacy reverse edge where
    :mod:`magi.bus.firmwares.books.file.skillsBook` reached into
    :mod:`magi.startup.paths`.

    Note: ``magi.startup`` itself is a composition root and is
    *expected* to import from the bus; the test only walks the
    bus subtree, not the startup subtree.
    """
    offenders: list[str] = []
    for path in (MAGI_ROOT / "bus").rglob("*.py"):
        for module, lineno in _imports(path):
            if module == "magi.startup" or module.startswith("magi.startup."):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno} -> {module}")
    assert not offenders, (
        "Bus must not import from the composition root "
        "(magi.startup); the bus layer should reach for its own "
        "resource resolvers:\n  " + "\n  ".join(offenders)
    )


def test_channels_do_not_import_startup_entry_points() -> None:
    """``channels`` is downstream of ``startup``, never the other way around.

    The runtime entry point (``run_magi``), the local supervisor
    (``start_magi``), the CLI parser, and the WebUI supervisor each
    own a process lifecycle.  Channels code must depend on those only
    by injection — never by importing the constructor.  ``WorkerRegistry``
    is explicitly allowed because the channel layer's wiring needs to
    type-annotate the injected instance; it does not spawn or start one.
    """
    forbidden = (
        "magi.startup.runtime",   # run_magi / RuntimeContext.create
        "magi.startup.local",     # start_magi / stop_magi / restart_magi
        "magi.startup.cli",       # main() / build_parser()
        "magi.startup.webui",     # run_webui_foreground / ControlContext / start_webui
    )
    offenders: list[str] = []
    for path in (MAGI_ROOT / "channels").rglob("*.py"):
        for module, lineno in _imports(path):
            if any(module == root or module.startswith(root + ".") for root in forbidden):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno} -> {module}")
    assert not offenders, (
        "channels must not reach back into startup entry points "
        "(composition-root constructors); inject the constructed "
        "instances instead:\n  " + "\n  ".join(offenders)
    )
