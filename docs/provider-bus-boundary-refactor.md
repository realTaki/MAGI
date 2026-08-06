# Provider-BUS 边界重构方案

## 1. 问题

`magi/providers/worker.py` 中的 `enqueue_llm_job` 函数存在位置不当：

```text
当前：
  magi/agent/worker.py      ──┐
  magi/agent/auto_title.py  ──┤   import
  magi/agent/compaction.py  ──┘
                                   ↓
                          magi.providers.worker.enqueue_llm_job  ← 错误位置
                                   ↓
                          magi.bus.store.BusStore.enqueue_llm_job
```

调用方（`magi.agent.*`）为了入队一个 LLM Job，被迫依赖 `magi.providers`。这违反了 [MAGI_MODULE_RESPONSIBILITIES_AND_DEPENDENCIES.md](./MAGI_MODULE_RESPONSIBILITIES_AND_DEPENDENCIES.md) 中的原则：

> `agent`、`tools`、`channels`、`plugins` 和 `proactive` 的运行时业务统一通过 BUS 协作，彼此不得直接依赖。

同时，`enqueue_llm_job` 与 `ProvidersWorker._invoke_provider` 之间共享一个**隐式的请求序列化格式**（dict 的键名和值类型），却没有在任何地方显式定义。生产端和消费端必须各自写一遍反序列化逻辑，容易脱节。

## 2. 目标

| 目标 | 说明 |
|------|------|
| 调用方只依赖 BUS | `magi.agent.*` 通过 `magi.bus` 发布 LLM Job，不 import `magi.providers` |
| Provider 只消费两种 Job | ProvidersWorker 只看到 LLM Job 和 Config Job，不关心调用方是谁 |
| 请求格式显式化 | 序列化/反序列化约定定义在 `magi/bus/protocols/` 中，生产端和消费端共享 |
| 删除 `enqueue_llm_job` | 从 `magi/providers/worker.py` 移除该函数及其模块级 `_worker` 单例的 notify 耦合 |

## 3. 方案概览

```text
重构后：
  magi/agent/worker.py      ──┐
  magi/agent/auto_title.py  ──┤   import
  magi/agent/compaction.py  ──┘
                                   ↓
                          magi.bus.llm.enqueue_llm_job     ← 新位置
                                   ↓
                          magi.bus.store.BusStore.enqueue_llm_job
                                   ↓
                          magi.bus.store.BusStore.notify_provider_worker()
                                   ↑
                          magi.providers.worker.ProvidersWorker
                            (只消费，不提供入队函数)
```

## 4. 详细步骤

### Step 1 — 在 `magi/bus/protocols/llm_jobs.py` 增加请求序列化格式

当前 `LLMJob` 只有字段定义，但没有 `to_request() / from_request()` 的序列化约定。增加两个纯函数：

```python
# magi/bus/protocols/llm_jobs.py 新增

def llm_job_to_request(job: LLMJob) -> dict[str, Any]:
    """Serialize an LLMJob into the dict shape the worker reads back."""
    return {
        "system": job.system,
        "messages": list(job.messages),
        "max_tokens": job.max_tokens,
        "tools": list(job.tools) if job.tools else None,
        "streaming": job.streaming,
        "extra": dict(job.extra),
    }

def request_to_chat_messages(request: dict[str, Any]) -> tuple[list[ChatMessage], dict[str, Any] | None, int, bool, str | None]:
    """Deserialize a persisted request dict back into provider-call args.
    
    Returns (chat_messages, tools_list, max_tokens, streaming, system_prompt).
    This is the single place the worker unpacks a request — no duplicate
    unpacking in provider code.
    """
    from magi.providers.provider import ChatMessage
    chat_messages = [
        ChatMessage(role=m["role"], content=m.get("content") or "")
        for m in request.get("messages") or []
    ]
    tools = list(request.get("tools") or []) or None
    max_tokens = int(request.get("max_tokens") or 1024)
    streaming = bool(request.get("streaming"))
    system = request.get("system")
    return chat_messages, tools, max_tokens, streaming, system
```

**注意**：`ChatMessage` 的 import 会让 `protocols/llm_jobs.py` 依赖 `magi.providers.provider`。这是可接受的，因为 `ChatMessage` 是纯数据类（无 SDK 依赖），且协议定义引用其自身的 DTO 类型是自然的。

### Step 2 — 在 `magi/bus/` 新增 `magi/bus/llm.py`

提供调用方使用的发布入口，对 `magi.providers` 零依赖：

```python
# magi/bus/llm.py (新文件)

"""LLM Job publishing — the single entry point for enqueuing LLM work.

All callers (agent turn, compaction, auto_title) use this module.
It hides the serialization format and worker-wake from callers.
"""

from __future__ import annotations

import logging
from typing import Any

from magi.bus.protocols.llm_jobs import LLMJob, llm_job_to_request

logger = logging.getLogger("magi.bus.llm")


async def enqueue_llm_job(job: LLMJob) -> str:
    """Publish an LLMJob onto the durable queue; return attempt_id.
    
    This replaces magi.providers.worker.enqueue_llm_job.
    """
    from magi.bus.bootstrap import get_bus_store
    store = get_bus_store()
    request = llm_job_to_request(job)
    try:
        result = store.enqueue_llm_job(
            run_id=job.run_id,
            request=request,
            inbox_event_id=job.inbox_event_id,
            kind=job.kind,
            hook_context=getattr(job, 'hook_context', None),
        )
        attempt_id = result.row_id
    except TypeError:
        # Legacy bus.store without request/hook_context kwargs
        attempt_id = store.enqueue_llm_job(
            run_id=job.run_id,
            inbox_event_id=job.inbox_event_id,
            kind=job.kind,
        )
        store.persist_llm_job_request(attempt_id, request=request)
    # Wake the local provider worker to reduce latency
    store.notify_provider_worker()
    return attempt_id
```

### Step 3 — 在 `BusStoreProtocol` 和 `BusStore` 增加 `notify_provider_worker()`

当前 `enqueue_llm_job` 直接访问模块级 `_worker` 单例来 wake，这耦合了 BUS 和 Provider 的进程内实现细节。改为通过 BUS 的接口：

```python
# magi/bus/protocols/agent.py — BusStoreProtocol 新增方法
def notify_provider_worker(self) -> None: ...
```

```python
# magi/bus/store.py — BusStore 实现
def notify_provider_worker(self) -> None:
    """Wake the local ProvidersWorker after a new LLM job is enqueued.
    
    No-op if the worker hasn't been started yet (e.g. during tests).
    """
    # Deferred import to avoid circular dependency at module load
    from magi.providers.worker import _worker
    if _worker is not None:
        _worker.notify()
```

`BusStore` → `magi.providers.worker` 是一个**运行时方向**的依赖（BUS 通知 Provider），但代码依赖方向不变：`magi.providers` 不依赖 `magi.bus.store` 的实现细节，`magi.bus.store` 通过 deferred import 访问 Provider Worker 单例。这与文档中 "BUS 是唯一的公共运行时接口" 一致——BUS 作为协调层，有权知道有哪些 Worker 需要唤醒。

### Step 4 — 在 `ProvidersWorker` 中使用共享的反序列化函数

```python
# magi/providers/worker.py — _invoke_provider 中替换手动解包

# 旧代码 (worker.py:313-320):
chat_messages = [
    ChatMessage(role=m["role"], content=m.get("content") or "")
    for m in request.get("messages") or []
]
tools = list(request.get("tools") or []) or None
streaming = bool(request.get("streaming"))
max_tokens = int(request.get("max_tokens") or 1024)
system = request.get("system")

# 新代码:
from magi.bus.protocols.llm_jobs import request_to_chat_messages
chat_messages, tools, max_tokens, streaming, system = request_to_chat_messages(request)
```

### Step 5 — 删除 `magi/providers/worker.py` 中的 `enqueue_llm_job`

移除 `worker.py` 底部的模块级 `enqueue_llm_job` 函数（当前 516-572 行）。保留 `_worker` 单例和 `start_provider_worker` / `stop_provider_worker`（它们仍然被 Composition Root 使用）。

更新 `worker.py` 的 `__all__`：

```python
# 移除 enqueue_llm_job
__all__ = [
    "ProvidersWorker",
    "start_provider_worker",
    "stop_provider_worker",
]
```

### Step 6 — 更新调用方 import

| 文件 | 旧 import | 新 import |
|------|-----------|-----------|
| `magi/agent/worker.py:183` | `from magi.providers.worker import enqueue_llm_job` | `from magi.bus.llm import enqueue_llm_job` |
| `magi/agent/auto_title.py:28` | `from magi.providers.worker import enqueue_llm_job` | `from magi.bus.llm import enqueue_llm_job` |
| `magi/agent/compaction.py:127` | `from magi.providers.worker import enqueue_llm_job` | `from magi.bus.llm import enqueue_llm_job` |

### Step 7 — 更新 `magi/providers/__init__.py` 的 re-export

当前 `__init__.py` 第 32 行 import 了 `LLMJob, LLMJobKind, LLMJobResult` 并 re-export。这些类型属于 `magi.bus.protocols`，`providers` 模块应该从那里导入而非反过来。保持现状即可（`providers` 作为 consumer 引用 protocol 定义是正常的），不需要改。

但检查是否有外部代码通过 `from magi.providers import LLMJob` 使用这些类型——如果有，改为直接从 `magi.bus.protocols.llm_jobs` 导入。

### Step 8 — 更新测试

| 测试文件 | 改动 |
|----------|------|
| `tests/integration/test_providers_worker.py` | 将 `from magi.providers.worker import enqueue_llm_job` 改为 `from magi.bus.llm import enqueue_llm_job` |

## 5. ControlJob → ConfigJob 重命名

### 5.1 动机

`ControlJob` 这个名字太模糊。"Control" 可以指向任何控制信号，但在 MAGI 的上下文中，Provider Worker 唯一消费的控制信号就是**配置变更**（provider 切换、API key 轮换、model 变更）。命名为 `ConfigJob` 更能表达：

- 这是配置层面的通知，不是业务层面的指令
- 与 `LLMJob` 形成清晰的二元对立：LLM Job = "调模型"，Config Job = "重建客户端"
- 对 Provider Worker 来说，两种 Job 就是它需要知道的一切

### 5.2 改名范围

| 位置 | 旧名 | 新名 |
|------|------|------|
| 协议文件 | `magi/bus/protocols/control_jobs.py` | `magi/bus/protocols/config_jobs.py` |
| 协议类型 | `ControlJobKind` | `ConfigJobKind` |
| ORM 模型文件 | `magi/bus/models/queue/control_job.py` | `magi/bus/models/queue/config_job.py` |
| ORM 模型类 | `ControlJob` | `ConfigJob`（`__tablename__` 保持 `"control_jobs"`） |
| BUS 方法 | `store.enqueue_control_job(...)` | `store.enqueue_config_job(...)` |
| BUS 方法 | `store.drain_control_jobs(...)` | `store.drain_config_jobs(...)` |
| 测试文件 | `tests/unit/test_control_jobs.py` | `tests/unit/test_config_jobs.py` |

**DB 表名不变**：`control_jobs` 表名保留。更名需要 ALTER TABLE 迁移，收益不足以覆盖风险。ORM 模型通过 `__tablename__ = "control_jobs"` 指向已有表。

### 5.3 协议层改动

```python
# magi/bus/protocols/config_jobs.py (原 control_jobs.py)

"""Wire format for the BUS's transient config-job queue.

A ConfigJob row is a short-lived signal between a BUS
producer (save_runtime_settings) and a BUS consumer
(ProvidersWorker). It acts only as "wake up and refresh".

Design principle
================

The worker does not branch on kind. Today the only kind is
"provider.config_changed". The payload is debug-only — it
must never carry the API key.
"""

from __future__ import annotations

from typing import Literal

ConfigJobKind = Literal[
    "provider.config_changed",
]

PROVIDER_CONFIG_CHANGED: ConfigJobKind = "provider.config_changed"

__all__ = [
    "ConfigJobKind",
    "PROVIDER_CONFIG_CHANGED",
]
```

### 5.4 ORM 模型改动

```python
# magi/bus/models/queue/config_job.py (原 control_job.py)

"""ORM table: control_jobs (transient provider-config refresh signal)."""

from sqlalchemy import Column, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped

from magi.db.base import Base


class ConfigJob(Base):          # 原 ControlJob
    __tablename__ = "control_jobs"   # 表名不变

    id: Mapped[int] = ...
    kind: Mapped[str] = ...         # Closed ConfigJobKind literal
    payload: Mapped[str | None] = ...
    created_at: Mapped[object] = ...
```

### 5.5 BusStore / BusStoreProtocol 方法改名

```python
# magi/bus/protocols/agent.py — BusStoreProtocol
def enqueue_config_job(           # 原 enqueue_control_job
    self, *, kind: str, payload: dict[str, Any] | None = None,
) -> str: ...
def drain_config_jobs(            # 原 drain_control_jobs
    self, *, worker_id: str, kind: str,
) -> int: ...
```

```python
# magi/bus/store.py — BusStore 实现
def enqueue_config_job(self, *, kind: str, payload=None) -> str:
    # 内部创建 ConfigJob(...) 实例
    ...

def drain_config_jobs(self, *, worker_id: str, kind: str) -> int:
    # 内部使用 ConfigJob 查询
    ...
```

### 5.6 受影响的文件清单

| 文件 | 改动 |
|------|------|
| `magi/bus/protocols/config_jobs.py` | **新建**（内容见 5.3）；旧 `control_jobs.py` 删除 |
| `magi/bus/protocols/__init__.py` | import 路径从 `.control_jobs` → `.config_jobs`；`ControlJobKind` → `ConfigJobKind` |
| `magi/bus/protocols/agent.py` | `BusStoreProtocol`：`enqueue_control_job` → `enqueue_config_job`；`drain_control_jobs` → `drain_config_jobs`；注释中 `control_jobs` 改为 `config_jobs` |
| `magi/bus/models/queue/config_job.py` | **新建**（内容见 5.4）；旧 `control_job.py` 删除 |
| `magi/bus/models/queue/__init__.py` | import 从 `control_job` → `config_job`；`ControlJob` → `ConfigJob` |
| `magi/bus/store.py` | import `ControlJob` → `ConfigJob`；方法名 `enqueue_control_job` → `enqueue_config_job`；`drain_control_jobs` → `drain_config_jobs` |
| `magi/bus/runtime_settings.py` | import 路径 `control_jobs` → `config_jobs`；调用 `store.enqueue_config_job(...)` |
| `magi/providers/worker.py` | import 路径 `control_jobs` → `config_jobs`；调用 `store.drain_config_jobs(...)`；注释 `control_jobs` → `config_jobs` |
| `tests/unit/test_config_jobs.py` | **新建**；旧 `test_control_jobs.py` 删除；内部 `ControlJob` → `ConfigJob`；方法名同步更新 |
| `tests/integration/test_providers_worker.py` | `enqueue_control_job` → `enqueue_config_job`；`drain_control_jobs` → `drain_config_jobs`；`ControlJob` → `ConfigJob`；import 路径更新 |
| `magi/bus/db/alembic/versions/0001_initial_schema.py` | 注释 `drain_control_jobs` → `drain_config_jobs`；表定义不变（`"control_jobs"`） |
| `magi/bus/db/alembic_runner.py` | 注释 `0007_control_jobs` → `0007_config_jobs` |

### 5.7 不改的

| 事项 | 原因 |
|------|------|
| DB 表名 `control_jobs` | 需要 ALTER TABLE 迁移，收益为零 |
| 列名、索引名（`ix_control_jobs_drain` 等） | 同上，DB 内部名称对代码语义无影响 |
| `PROVIDER_CONFIG_CHANGED` 常量值 | `"provider.config_changed"` 字符串本身语义清晰，且已持久化到 DB 行中 |

## 6. 最终模块依赖关系

```text
重构前：
  magi.agent.* ──→ magi.providers.worker   ← 违规：agent 依赖 providers
  magi.providers.worker ──→ magi.bus.store

重构后：
  magi.agent.* ──→ magi.bus.llm ──→ magi.bus.store
                                       ↑
  magi.providers.worker ──────────────┘  (只消费，不提供入队)
```

符合文档规范：
- `magi.agent` → `magi.bus` ✓
- `magi.agent` → `magi.providers` ✗（不再存在）
- `magi.providers` → `magi.bus`（通过 `BusStoreProtocol`）✓

## 7. Provider 视角的简化

对 Provider 来说，它需要关心的两种 Job：

| Job 类型 | 来源 | 消费方式 |
|----------|------|----------|
| LLM Job | `BusStore.claim_next_llm_job()` | 读取 request JSON，调用 `provider.chat()/stream()`，写回 response/error |
| Config Job | `BusStore.drain_config_jobs(kind="provider.config_changed")` | 触发 `_rebuild_provider()` |

Provider 不需要知道：
- Job 是 agent turn、compaction 还是 auto_title（`kind` 字段仅审计用）
- 调用方的 `extra` payload 里有什么
- `hook_context` 的内容
- 结果如何被调用方消费（`provider.completed` inbox 事件由 BUS 层路由）

这已经是当前设计的意图，重构只是把 `enqueue_llm_job` 的位置纠正到 BUS 层，让这个意图在代码结构上也得到体现。

## 8. 不做的

| 事项 | 原因 |
|------|------|
| 把 `LLMJob` 的 `kind` 改成 Provider 内部枚举 | `kind` 是调用方设置的审计标签，Provider 本来就不分支它 |
| 把 `extra` 改成 typed payload | `extra` 是调用方不透明数据，Provider 不解析，保持 `dict` 即可 |
| 拆出独立的 `magi/providers/queue.py` | 过度拆分；Provider 只需要知道"从 BUS 取 Job"，入队逻辑属于 BUS 层 |
| 让 Provider Worker 订阅 BUS 事件来感知新 Job | 当前 poll 模型（0.25s）简单可靠，改为事件驱动增加复杂度但收益有限 |
