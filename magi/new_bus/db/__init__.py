"""new_bus.db — 独立于旧 bus 的全新数据库抽象层。

Public surface:

- ``Base`` / ``utcnow_naive`` — SQLAlchemy declarative base + UTC helper
  (new_bus 自有, 不依赖 ``magi.bus.db.base``)
- ``EngineFactory`` — dialect-aware engine creator; multiple instances
  can coexist (e.g. one local SQLite, one MAGIS PG)
- ``build_local_factory`` / ``build_magis_factory`` — convenience
  constructors for the two production deployments
- ``FileStore`` — file-system counterpart to EngineFactory, with
  Format plugins (TextFormat / YamlFormat / JsonFormat)
"""

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.db.engine import (
    EngineFactory,
    build_local_factory,
    build_magis_factory,
)
from magi.new_bus.db.file import (
    DEFAULT_FORMATS,
    FileStore,
    FileStoreError,
    Format,
    FormatError,
    JsonFormat,
    PathError,
    TextFormat,
    YamlFormat,
)

__all__ = [
    "Base",
    "utcnow_naive",
    "EngineFactory",
    "FileStore",
    "build_local_factory",
    "build_magis_factory",
    # Format types
    "DEFAULT_FORMATS",
    "FileStoreError",
    "Format",
    "FormatError",
    "JsonFormat",
    "PathError",
    "TextFormat",
    "YamlFormat",
]
