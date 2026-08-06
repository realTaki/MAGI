"""new_bus.db — 独立于旧 bus 的全新数据库抽象层。

Public surface:

- ``Base`` / ``utcnow_naive`` — SQLAlchemy declarative base + UTC helper
  (new_bus 自有, 不依赖 ``magi.bus.db.base``)
- ``EngineFactory`` — dialect-aware engine creator; multiple instances
  can coexist (e.g. one local SQLite, one MAGIS PG)
- ``build_local_factory`` / ``build_magis_factory`` — convenience
  constructors for the two production deployments
"""

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.db.engine import (
    EngineFactory,
    build_local_factory,
    build_magis_factory,
)

__all__ = [
    "Base",
    "utcnow_naive",
    "EngineFactory",
    "build_local_factory",
    "build_magis_factory",
]
