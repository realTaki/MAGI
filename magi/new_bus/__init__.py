"""new_bus — 全新的消息总线模块。

与旧 ``magi.bus`` 并行、独立。 提供自己的 ORM 基类、EngineFactory、
Books（读侧 CRUD）、Jobs（写侧 publish/claim/submit_result），以及
自动发现双数据库（local SQLite + MAGIS PG）的统一门面 ``NewBus``。

Worker 入口::

    from magi.new_bus import get_bus

    bus = get_bus()
    job = bus.tool_jobs.claim(worker_id="w1")
    magic = bus.magic.get(magic_id=1)

需要具体的 DTO / Book / Job 类型时，直接从子模块导入：:

    from magi.new_bus.books.local import SessionBook
    from magi.new_bus.jobs import RunToolJob, runToolJob
"""

from magi.new_bus.bootstrap import NewBus, get_bus, set_magis_url
from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.db.engine import EngineFactory, build_local_factory, build_magis_factory

__all__ = [
    "NewBus",
    "get_bus",
    "set_magis_url",
    "Base",
    "utcnow_naive",
    "EngineFactory",
    "build_local_factory",
    "build_magis_factory",
]
