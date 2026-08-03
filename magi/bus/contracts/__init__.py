"""Public BUS contracts.

The package layout is the target architecture.  Until the legacy flat module
is split into domain files, load it under a private module name and re-export
its public DTOs here.  No domain package is imported by this bridge.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_legacy() -> object:
    spec = importlib.util.spec_from_file_location(
        "magi.bus._legacy_contracts", Path(__file__).parent.parent / "contracts.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_legacy = _load_legacy()
for _name in dir(_legacy):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_legacy, _name)

__all__ = tuple(name for name in dir(_legacy) if not name.startswith("_"))

# New domain contracts live in real package modules.  Keep the flat legacy
# module bridge above for the existing actor DTOs while making these symbols
# available from the single public ``magi.bus.contracts`` entry point.
from magi.bus.contracts.magis import ProviderConfiguration  # noqa: E402

__all__ = (*__all__, "ProviderConfiguration")
