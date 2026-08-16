"""bus — 消息总线模块。

提供 ORM 基类、EngineFactory、Books（读侧 CRUD）、
Guild（写侧 publish/claim/submit_result）、FileShelf（文件 I/O + 热重载），
以及通过 :func:`open_bus` 构造的统一门面 ``Bus``。

``Bus`` 由组合根（:mod:`magi.startup.runtime`）显式构造，通过
**构造器注入**传入各 Worker。没有进程级单例——调用方依赖显式传入的
``Bus`` 实例，不通过全局变量取回::

    from magi.bus import open_bus

    bus = open_bus(state_dir="/path/to/memories", magis_url="...")
    worker = AgentWorker(bus=bus)    # 构造器注入
    job = bus.tool_job_board.claim()
    adam = bus.memberships_book.get(1)  # ADAM = membership id=1

需要具体的 Book / Job 类型时，直接从子模块导入::

    from magi.bus.library.local import SessionBook  # SessionBook is a backward-compat alias for ConversationBook
    from magi.bus.library.file import PromptBook
    from magi.bus.guild import RunToolJob, runToolJobBoard

底层设施（``Base`` / ``utcnow_naive`` / ``EngineFactory`` / ``FileShelf``）
同理，从 :mod:`magi.bus.db` 导入。
"""

from __future__ import annotations

from magi.bus.bootstrap import Bus, MagisBus, open_bus, open_magis_bus

__all__ = [
    "Bus",
    "MagisBus",
    "open_bus",
    "open_magis_bus",
]
