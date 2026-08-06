"""new_bus.books — 只读数据簿，即查即得，无限并发。"""

from magi.new_bus.books.base import BaseBook
from magi.new_bus.books.SessionBook import Session, SessionBook
from magi.new_bus.books.MemoryBook import Memory, MemoryBook

__all__ = [
    "BaseBook",
    "Session",
    "SessionBook",
    "Memory",
    "MemoryBook",
]
