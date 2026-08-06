"""ToolCatalogStateBook + ToolDefinitionBook — durable Tool Catalog.

Two tables:
- ``tool_catalog_state`` — singleton (id=1) holding the monotonic
  catalog revision + snapshot hash
- ``tool_definitions``   — one row per catalog tool

Schema mirrors the old bus's ``tool_catalog_state`` + ``tool_definitions``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import (
    BigInteger,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolCatalogState:
    id: int
    revision: int
    snapshot_hash: str


@dataclass(frozen=True, slots=True)
