"""new_bus.db — 数据库抽象层。"""

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.db.engine import EngineFactory, get_engine, get_session, open_session

__all__ = [
    "Base",
    "utcnow_naive",
    "EngineFactory",
    "get_engine",
    "get_session",
    "open_session",
]
