"""BUS domain services, retained behind the new package namespace."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_spec = importlib.util.spec_from_file_location(
    "magi.bus._legacy_services", Path(__file__).parent.parent / "services.py"
)
assert _spec is not None and _spec.loader is not None
_legacy = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _legacy
_spec.loader.exec_module(_legacy)
for _name in dir(_legacy):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_legacy, _name)

__all__ = tuple(name for name in dir(_legacy) if not name.startswith("_"))
