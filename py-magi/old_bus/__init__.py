"""bus — 消息总线模块。

BUS 分成两层：

- :mod:`bus.bases` — Job/Book 基类，以及 ``db``（ORM / engine / FileShelf）
- :mod:`bus.firmwares` — 具体 Job Boards 与 Books

组合根通过 :func:`open_bus` 构造统一门面 ``Bus``，再经**构造器注入**
传入各 Worker。没有进程级单例::

    from bus import open_bus

    bus = open_bus(workspace_dir="/path/to/workspace", magis_url="...")
    worker = AgentWorker(bus=bus)
    job = bus.tool_job_board.claim(worker_id="worker-1")
    adam = bus.memberships_book.get(1)

需要具体的 Book / Job 类型时，从 firmwares 导入::

    from bus.firmwares.books.local import ConversationBook
    from bus.firmwares.books.file import PromptBook
    from bus.firmwares.jobs import RunToolJob, runToolJobBoard

基类从 :mod:`bus.bases` 导入；底层存储从 :mod:`bus.bases.db`
导入。领域代码不得导入 ``bus.bases.db``。
"""

from __future__ import annotations

from old_bus.bootstrap import Bus, MagisBus, open_bus

__all__ = [
    "Bus",
    "MagisBus",
    "open_bus",
]
