"""new_bus — 全新的消息总线模块。

与旧 ``magi.bus`` 并行、独立。 提供自己的 ORM 基类、EngineFactory、
Books（读侧 CRUD）、Guild（写侧 publish/claim/submit_result），以及
通过 :func:`bootstrap_new_bus` 构造的统一门面 ``NewBus``。

``NewBus`` 由组合根（:mod:`magi.startup.runtime`）显式构造并传入 worker::

    from magi.new_bus import bootstrap_new_bus

    bus = bootstrap_new_bus(state_dir="/path/to/memories", magis_url="...")
    job = bus.tool_job_board.claim()
    adam = bus.memberships_book.get(magic_id=1)  # ADAM = membership id=1

需要具体的 DTO / Book / Job 类型时，直接从子模块导入：:

    from magi.new_bus.library.local import SessionBook
    from magi.new_bus.guild import RunToolJob, runToolJobBoard
"""

from magi.new_bus.bootstrap import NewBus, bootstrap_new_bus
from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.db.engine import EngineFactory, build_local_factory, build_magis_factory

__all__ = [
    "NewBus",
    "bootstrap_new_bus",
    "Base",
    "utcnow_naive",
    "EngineFactory",
    "build_local_factory",
    "build_magis_factory",
]
