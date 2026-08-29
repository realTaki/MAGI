"""bus.bases.db — 数据库集成层，不含业务表定义。

Public surface:

- ``Base`` / ``utcnow_naive`` — SQLAlchemy declarative base + UTC helper
- ``EngineFactory`` — dialect-aware engine creator; multiple instances
  can coexist (e.g. one local SQLite and one MAGIS SQLite or PostgreSQL store)
- ``build_local_factory`` / ``build_magis_factory`` — convenience
  constructors for the two production deployments
- ``FileShelf`` — file-system counterpart to EngineFactory, with
  Format plugins (TextFormat / YamlFormat / JsonFormat)
"""

from old_bus.bases.db.base import Base, utcnow_naive
from old_bus.bases.db.engine import (
    EngineFactory,
    build_local_factory,
    build_magis_factory,
)
from old_bus.bases.db.file import (
    DEFAULT_FORMATS,
    FileShelf,
    FileShelfError,
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
    "FileShelf",
    "build_local_factory",
    "build_magis_factory",
    # Format types
    "DEFAULT_FORMATS",
    "FileShelfError",
    "Format",
    "FormatError",
    "JsonFormat",
    "PathError",
    "TextFormat",
    "YamlFormat",
]
