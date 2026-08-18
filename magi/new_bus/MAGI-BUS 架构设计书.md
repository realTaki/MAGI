# MAGI-BUS 架构设计书

**状态：** 实现草案  
**目标版本：** MAGI-BUS vNext  
**当前部署模型：** 一个 MAGI 一个进程  
**用途：** BUS 重构与实现基线

---

# 1. 设计目标

MAGI-BUS 是一个 MAGI 内部的共享软件总线。

它的目标不是成为：

- Plugin Framework；
- Workflow Engine；
- Plugin Manager；
- Scheduler；
- Orchestrator；
- Service Locator。

BUS 的目标是提供一组足够简单、稳定的基础机制，使 MAGI 中不同模块能够：

- 不直接依赖彼此；
- 不直接访问共享数据库；
- 通过统一的 Job 协议交换工作；
- 通过 BUS 内部维护的 Book 保存共享状态；
- 通过 Firmware 获得稳定的数据与行为协议；
- 通过 Job Slot 在固定生命周期节点插入外部行为。

核心目标是：

> **模块依赖 BUS Firmware，而不是依赖其他模块。**

---

# 2. 核心设计哲学

MAGI-BUS 的设计借鉴硬件 BUS。

一个硬件模块：

- 知道总线协议；
- 知道自己需要读写什么；
- 不需要知道数据由哪个设备产生；
- 不需要知道自己的输出最终会被哪个设备消费。

MAGI 中也应该如此：

```text
Agent ─────┐
Tools ─────┤
Channels ──┤
Plugins ───┤
           ▼
          BUS
```

而不是：

```text
Agent ─────→ Tools
Tools ─────→ Agent
Plugin A ──→ Plugin B
```

模块之间应该通过 BUS 形成**逻辑解耦**。

---

# 3. 当前进程模型

当前版本采用：

> **一个 MAGI = 一个进程。**

例如：

```text
┌──────────────────── MAGI Process ────────────────────┐
│                                                     │
│  Launcher                                           │
│     │                                               │
│     ├── BUS                                         │
│     ├── Agent                                       │
│     ├── Tools                                       │
│     ├── Channels                                    │
│     └── Plugins / Docks                             │
│                                                     │
└─────────────────────────────────────────────────────┘
```

但是：

> **同进程不意味着允许模块直接耦合。**

即使实际上只是 Python 对象之间的调用，逻辑上仍然应该：

```text
Component
    │
    ▼
   BUS
```

而不是：

```text
Component A
    │
    ▼
Component B
```

BUS 当前不定义：

- IPC；
- TCP；
- Unix Socket；
- Named Pipe；
- 多进程；
- Remote Worker。

这些全部留给未来的 Launcher / Runtime。

因此：

> **BUS 定义逻辑拓扑，不定义部署拓扑。**

---

# 4. BUS 的职责边界

BUS 负责：

- Backend 抽象；
- Book；
- Job；
- JobBoard；
- ManageBookJobBoard；
- Job 生命周期；
- Job Slot；
- Slot attach / detach；
- Firmware；
- Firmware compatibility；
- Book 与 Job 的持久化。

BUS 不负责：

- Plugin 安装；
- Plugin 卸载；
- Plugin discovery；
- Plugin enable / disable；
- Plugin 生命周期；
- Plugin dependency；
- Plugin priority；
- Hook ordering；
- Dock；
- 多 Hook composition；
- Worker routing；
- Launcher；
- Process supervision。

核心原则：

> **BUS 管理自己的协议和插槽，不管理插入插槽的组件。**

---

# 5. 总体结构

新版 BUS 分成两个主要层次：

```text
                    External Components

            Agent / Tools / Plugins / Channels
                         │
                         │
                         ▼
┌──────────────────────────────────────────────┐
│                 bus.firmware                 │
│                                              │
│          Concrete Books + Concrete Jobs      │
│               Firmware Version               │
└─────────────────────┬────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────┐
│                   bus.base                   │
│                                              │
│     Backend / Book / Job / JobBoard          │
│        ManageBookJobBoard / Job Slots          │
└─────────────────────┬────────────────────────┘
                      │
                      ▼
               Storage Backend
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
        File        SQLite    PostgreSQL
```

最核心的分工：

> **Base 定义机制。Firmware 定义具体协议。**

---

# 6. `bus.base`

`bus.base` 是 BUS 最稳定的底层。

它不能包含 MAGI 业务概念。

例如 Base 不应该知道：

- Agent；
- Tool；
- Channel；
- LLM；
- Message；
- Memory；
- Telegram。

Base 只提供几个通用 primitive：

```text
Backend
Book
Job
JobBoard
ManageBookJobBoard
Slot
```

其中 Slot 是 Job 的组成能力，而不是与 Job 平级的业务系统。

---

# 7. Backend

## 7.1 Backend 的作用

Backend 负责回答：

> **Book 和 Job 实际存在哪里？**

Base 只依赖统一 Backend 接口。

```text
Book / JobBoard
      │
      ▼
   Backend
      │
 ┌────┼─────────────┐
 ▼    ▼             ▼
File SQLite    PostgreSQL
```

---

# 8. Backend 实现

正式 Backend 应当对应真实持久化方式。

目录建议：

```text
bus/
└── backends/
    ├── file/
    ├── sqlite/
    └── postgres/
```

或者较简单地：

```text
backends/
├── file.py
├── sqlite.py
└── postgres.py
```

具体组织方式根据代码量决定。

---

## 8.1 File Backend

适用于：

- 极简部署；
- 本地 standalone；
- 可读性优先；
- 不希望依赖数据库服务的环境。

可以使用：

```text
JSON
JSONL
SQLite-like file structures
其他本地格式
```

具体文件编码不属于 BUS 协议。

---

## 8.2 SQLite Backend

建议作为 MAGI 本地默认 Backend。

优点：

- 单文件；
- 支持 transaction；
- 无独立数据库服务；
- SQL 查询能力完整；
- Alembic 支持成熟。

---

## 8.3 PostgreSQL Backend

适用于未来：

- Server deployment；
- 更复杂并发；
- MAGIS；
- 多实例或远程存储。

---

# 9. 测试用内存实现

如果测试时需要内存 Fake：

```text
FakeBackend
InMemoryBackend
```

应该位于：

```text
tests/
```

或者：

```text
testing/
```

而不是正式：

```text
bus/backends/
```

因为它属于测试工具，而不是 MAGI-BUS 的正式存储能力。

---

# 10. Schema 与 Migration

数据库 Schema 不需要在 BUS 中另外建立一套独立 Schema 系统。

使用：

> **SQLAlchemy Model + Alembic**

即可。

Firmware 中定义的 Book / Job 持久化结构发生变化时：

```text
Model change
     │
     ▼
Alembic migration
```

因此不需要：

```text
firmware/schemas/
```

这种额外层。

建议：

```text
firmware/
└── migrations/
    ├── env.py
    └── versions/
```

或者根据现有 MAGI 数据库结构与全局 Alembic 合并管理。

---

# 11. Book

Book 表示：

> **BUS 当前保存的状态。**

例如 Firmware 未来可能定义：

```text
ToolBook
AgentBook
ChannelBook
SessionBook
```

Book 底层可能对应：

```text
SQL Table
File Collection
PostgreSQL Table
```

但这些属于 Backend 实现。

---

# 12. Book 是 BUS 内部对象

这是新版 BUS 的硬性规则：

> **BUS 外部模块不能直接访问 Book。**

不仅不能修改，也不应该读取 Book。

禁止：

```python
tool_book.list()
tool_book.update(...)
```

禁止：

```text
Plugin → Database
```

禁止：

```text
Agent → Book
```

Book 属于 BUS 内部状态。

---

# 13. 外部组件只操作 Job

外部模块真正与 BUS 交互的主要对象应该是：

> **Job / JobBoard。**

例如：

```text
Plugin
   │
   │ publish Job
   ▼
 JobBoard
```

或者：

```text
Worker
   │
   │ claim
   ▼
 JobBoard
```

如果某类操作需要修改共享状态：

```text
External Component
        │
        ▼
 ManageBookJob
        │
        ▼
ManageBookJobBoard
        │
        ▼
      Book
```

这样外部组件永远不需要接触 Book。

---

# 14. 为什么 Book Mutation 必须经过 Job

如果允许：

```text
Plugin → Book.update()
```

那么 Book 中的某条记录变化以后，很难天然知道：

> 谁改的？

而如果统一：

```text
Plugin
   │
   ▼
ManageBookJob
   │
   ▼
Book
```

那么 Job 本身就已经记录了：

- 谁发布；
- 什么时候发布；
- 要求做什么；
- 操作成功还是失败。

因此：

> **Job History 本身就是 Audit Trail。**

不需要另外建立：

```text
Audit
AuditRecord
MutationProvenance
```

等系统。

---

# 15. Job 是行为历史

Book 与 Job 的关系可以定义为：

> **Book 保存当前结果。**

> **Job 保存产生系统行为的过程。**

例如：

```text
ToolBook
```

回答：

> 当前有哪些 Tool。

而：

```text
EditToolBookJobBoard
```

回答：

> Tool 曾经发生过哪些注册、更新和删除操作。

因此无需再建立一套独立 Event Log 或 Audit Log。

---

# 16. 每个 Book 自带 ManageBookJobBoard

每个具体 Book 都应该对应一个内部的：

```text
ManageBookJobBoard
```

例如：

```text
ToolBook
└── EditToolBookJobBoard

AgentBook
└── EditAgentBookJobBoard
```

这里的关系应当在 Firmware 中定义。

---

# 17. ManageBookJob

ManageBookJob 是特殊 Job。

它与普通 Job 最大区别是：

> **不需要被外部 Worker claim。**

它在 publish 后由 BUS 自动处理。

例如：

```text
Plugin
   │
   │ publish
   ▼
EditToolBookJobBoard
   │
   ▼
  BUS
   │
   ├── validate
   ├── edit ToolBook
   └── update Job status
```

---

# 18. ManageBookJob 生命周期

可以非常简单：

```text
PENDING
   │
   ▼
BUS executes
   │
   ├────→ COMPLETED
   │
   └────→ FAILED
```

或者沿用所有 Job 的统一状态定义。

关键不是状态名字。

关键是：

> ManageBookJob 不进入外部 claim 流程。

---

# 19. ManageBookJob 不删除

即使 Book edit 已经完成：

```text
ManageBookJob
```

仍然应该留在 JobBoard。

因此以后可以查询：

```text
哪个模块在什么时候请求了这次修改？
```

不需要 BUS 再额外维护 Audit。

---

# 20. Job

Job 表示：

> **某件需要发生、正在发生或者曾经发生的事情。**

普通 Job 可能包含：

```text
id
type
status
publisher
created_at
payload
result
error
```

根据实际需求还可以增加：

```text
priority
available_at
attempt
```

但第一版应尽量避免预先加入没有实际用途的字段。

---

# 21. 不预设复杂 Trace 字段

当前 BUS 不需要强制加入：

```text
correlation_id
causation_id
mutation_provenance
trace_id
span_id
```

这些属于更复杂的事件追踪或分布式 tracing 机制。

如果将来发现确实需要：

> Job B 是由哪个 Job A 直接触发的？

可以再考虑一个简单的：

```text
parent_job_id
```

但不进入当前 BUS v1 基础模型。

---

# 22. 不强制 Book Revision

当前版本也不把：

```text
Revision
MVCC
CAS
```

作为所有 Book 的基础能力。

因为 Book mutation 已经统一经过：

```text
ManageBookJobBoard
```

BUS 可以首先通过确定的 Job 处理顺序保证 mutation 行为。

未来某个 Book 如果确实存在：

```text
lost update
compare-and-swap
optimistic locking
```

需求，可以由那个具体 Book / Firmware 协议扩展。

不需要让所有 Book 一开始承担这个复杂度。

---

# 23. JobBoard

JobBoard 是 Job 的运行容器，同时自己提供操作 API。

因此不需要再建立：

```text
firmware/apis/
```

一层额外包装。

JobBoard 可以直接提供：

```python
publish(...)
claim(...)
complete(...)
fail(...)
list(...)
get(...)
```

具体方法根据 JobBoard 类型决定。

---

# 24. JobBoard 是主要外部接口

从组件视角：

```text
Plugin
   │
   ▼
JobBoard
```

JobBoard 本身就是 BUS Contract 的一部分。

因此：

> **JobBoard API 就是 API。**

没有必要出现：

```text
API layer
   ↓
JobBoard API
```

这种重复抽象。

---

# 25. 普通 Job 生命周期

普通 Worker Job 可以采用简单生命周期：

```text
PENDING
   │
   ▼
CLAIMED
   │
   ├────→ COMPLETED
   │
   └────→ FAILED
```

如果后续发现需要：

```text
RUNNING
RETRYING
EXPIRED
```

再加入即可。

第一版不应该为了理论完整性提前建立复杂状态机。

---

# 26. Claim

普通 Job 可以被 Worker claim。

例如：

```text
ToolCallJob
    │
    ▼
claim
    │
    ▼
Tool Worker
```

同一个 Job 的 claim 必须具有明确的一次性 ownership 语义，避免两个不同 Worker 同时认为自己拿到了同一个 Job。

具体并发控制方式由 Backend 实现。

---

# 27. Slot

Slot 不是独立于 Job 的业务模块。

Slot 应该理解成：

> **Job 生命周期本身提供的一种 Feature。**

因此不会存在：

```text
firmware/
├── jobs/
└── slots/
```

而应该是：

```text
Firmware Job
    │
    └── Slots
```

---

# 28. Job 的标准 Slot

一个 Job 可以具有：

```text
pre_publish
publish
post_publish

pre_claim
claim
post_claim
```

这些 Slot 是 Job lifecycle 上的固定 Hook Point。

概念上：

```text
Job.publish()
      │
      ▼
 pre_publish
      │
      ▼
   publish
      │
      ▼
 post_publish
```

以及：

```text
Job.claim()
      │
      ▼
  pre_claim
      │
      ▼
    claim
      │
      ▼
 post_claim
```

---

# 29. Slot 是 Job Base Feature

Base Job 可以提供通用 Slot primitive。

概念上：

```python
class Job:
    pre_publish
    publish
    post_publish

    pre_claim
    claim
    post_claim
```

具体 Firmware Job 可以根据自己的语义使用这些能力。

因此：

> Slot 属于 Job model，而不是单独的一套 Firmware Domain。

---

# 30. Slot Cardinality

当前 BUS 只需要两种 Slot：

```text
SINGLE
MULTI
```

---

# 31. SINGLE Slot

以下 Slot 默认为：

```text
SINGLE
```

包括：

```text
pre_publish
post_publish

pre_claim
claim
post_claim
```

一个 SINGLE Slot：

```text
0 或 1 个 Handler
```

如果：

```text
Plugin A
```

已经接入：

```text
pre_claim
```

那么 Plugin B 再尝试：

```text
attach(pre_claim)
```

BUS 直接返回：

```text
SLOT_OCCUPIED
```

结束。

---

# 32. BUS 不解决 SINGLE Slot 冲突

BUS 不做：

```text
谁优先？
谁先加载？
谁版本高？
谁 priority 大？
```

也不会自动组成：

```text
Plugin A → Plugin B → Plugin C
```

它只维护：

> 这个 Slot 当前有没有被占用。

---

# 33. MULTI Publish Slot

`publish` 是例外。

它允许任意数量 Handler 接入。

例如：

```text
                    ┌── Plugin A
                    │
                    ├── Plugin B
Job.publish ────────┤
                    ├── Plugin C
                    │
                    └── Plugin N
```

不存在数量上限。

---

# 34. 为什么 Publish 可以 MULTI

Publish 的核心语义是：

> **广播 / fan-out。**

多个 Listener 都只是独立接收同一次 Publish。

例如：

```text
A receives Job
B receives Job
C receives Job
```

A 是否存在不会改变 B 能不能收到。

因此不产生控制权冲突。

---

# 35. 为什么 Pre/Post/Claim 必须 SINGLE

这些 Slot 处于控制路径。

例如两个独立 Handler 同时处理：

```text
pre_claim
```

一个：

```text
PASS
```

另一个：

```text
REJECT
```

BUS 就必须决定：

```text
谁先？
谁赢？
```

这不是 BUS 应该做的事情。

所以：

```text
pre_claim = SINGLE
```

从协议层直接消灭这种歧义。

---

# 36. BUS 不支持 Priority

特别需要明确：

BUS 不提供：

```text
priority = 10
priority = 20
priority = 30
```

这种 Hook 排序机制。

BUS 也不会为了让多个 Hook 接入而增加：

```text
pre_claim.security
pre_claim.policy
pre_claim.quota
```

这种动态拓扑。

因为这些都属于：

> **控制逻辑组合。**

不是 BUS 的职责。

---

# 37. Dock

如果多个 Plugin 确实需要共享一个 SINGLE Slot：

```text
Security
Policy
Quota
```

应该由 BUS 外部创建：

```text
Dock
```

例如：

```text
Security ─┐
Policy ───┼── Dock ─────→ BUS.pre_claim
Quota ────┘
```

Dock 自己可以拥有：

```text
priority
ordering
pipeline
parallel execution
routing
fallback
```

但 BUS 看见的只有：

```text
pre_claim
   │
   ▼
ONE Handler
```

---

# 38. Launcher

Launcher 位于 BUS 外部。

当前单进程结构：

```text
Launcher
   │
   ├── create BUS
   ├── load Firmware
   ├── create Agent
   ├── create Tools
   ├── create Channels
   ├── load Plugins
   └── create Docks if necessary
```

Launcher 可以聪明。

BUS 应该保持简单。

---

# 39. BUS 不知道 Plugin

严格来说，BUS 甚至不需要把接入 Slot 的对象理解成 Plugin。

BUS 只需要知道：

```text
Handler
```

或者：

```text
Endpoint
```

这个对象满足对应 Slot 所要求的 callable / interface 即可。

因此 BUS 不需要知道：

```text
Plugin name
Plugin package
Plugin install state
Plugin dependency
```

---

# 40. Slot Attach

BUS 提供：

```python
attach(slot, handler)
```

对于 SINGLE：

```text
EMPTY
  │
  │ attach A
  ▼
BOUND(A)
```

第二个：

```text
attach B
```

得到：

```text
SlotOccupiedError
```

---

# 41. Slot Detach

BUS 同时提供：

```python
detach(slot, handler)
```

或者：

```python
binding = attach(...)
detach(binding)
```

当前一个 MAGI 一个进程，因此无需：

```text
lease
heartbeat
process liveness
network timeout
```

这些机制。

Plugin 生命周期由 Launcher 管理。

---

# 42. Firmware

Firmware 定义：

> **当前 MAGI BUS 具体有哪些 Book 和 Job，以及它们分别意味着什么。**

例如：

```text
Firmware
├── ToolBook
├── AgentBook
│
├── ToolCallJob
├── AgentRunJob
└── ...
```

Base 完全不知道这些具体名字。

---

# 43. Firmware 目录

建议：

```text
bus/
├── base/
│   ├── backend.py
│   ├── book.py
│   ├── job.py
│   └── job_board.py
│
├── backends/
│   ├── file/
│   ├── sqlite/
│   └── postgres/
│
├── firmware/
│   ├── books/
│   │   ├── tool.py
│   │   ├── agent.py
│   │   └── ...
│   │
│   ├── jobs/
│   │   ├── tool_call.py
│   │   ├── agent_run.py
│   │   └── ...
│   │
│   ├── migrations/
│   │   └── ...
│   │
│   └── version.py
│
└── bus.py
```

这里不存在：

```text
views/
apis/
slots/
audit/
causality/
revision/
schemas/
```

这些额外层。

---

# 44. 为什么没有 `slots/`

因为：

```text
Slot
```

是：

```text
Job Feature
```

而不是 Firmware 中与 Job 平行的 Domain。

---

# 45. 为什么没有 `views/`

因为外部根本不直接访问 Book。

没有：

```text
Book → View → Plugin
```

这条对外数据链。

因此没有必要为隐藏 ORM 再建立一整层 View architecture。

---

# 46. 为什么没有 `apis/`

因为：

```text
JobBoard
```

本身已经有 API。

额外：

```text
API → JobBoard API
```

没有必要。

---

# 47. 为什么没有 `audit/`

因为：

```text
Job History
```

本身就是操作历史。

特别是：

```text
ManageBookJobBoard
```

天然就是对应 Book 的变更历史。

---

# 48. 为什么没有 `revision/`

Revision 不是所有 Book 必需的机制。

第一版不强迫所有 Book 支持 optimistic concurrency。

有需要时再由特定 Firmware Book 引入。

---

# 49. 为什么没有 `schemas/`

Schema 来源于：

```text
Book / Job persistence model
```

而 Migration 由：

```text
Alembic
```

负责。

不需要再描述一次。

---

# 50. Firmware Version

这里仍建议保留一个极轻量：

```python
FIRMWARE_VERSION = ...
```

它不是数据库 Schema Version。

它表达的是：

> **当前 BUS Firmware 对外兼容到哪个协议版本。**

例如 Plugin 可以声明：

```text
requires firmware >= 3
```

BUS：

```text
firmware = 5
```

则可以使用。

---

# 51. 为什么 Firmware Version 不直接等于 Alembic Revision

Alembic 管：

> 数据库结构怎么从 A 升级到 B。

Firmware Version 管：

> Plugin 能不能理解当前 BUS 协议。

这两者经常相关，但语义不同。

例如：

```text
修改数据库 index
```

可能需要一个 Alembic Migration，却完全没有改变 Plugin ABI。

反过来：

```text
增加一个新的 Job 类型
```

可能改变 Firmware 能力，但不一定修改数据库 Schema。

所以仍建议：

```text
Alembic
    = storage migration

Firmware Version
    = protocol compatibility
```

但 Firmware Version 只需要一个简单常量，不需要复杂版本系统。

---

# 52. Error Model

第一版只需要非常直接的错误。

例如：

```text
BackendError

BookNotFoundError

JobNotFoundError
InvalidJobError
InvalidJobStateError
JobAlreadyClaimedError

SlotNotFoundError
SlotOccupiedError

FirmwareCompatibilityError
```

不要为了完整性提前制造大量细粒度 exception。

---

# 53. Public BUS Surface

BUS 对外 API 应该尽量小。

核心可以收敛为：

```python
bus.publish(...)
bus.claim(...)

bus.attach(...)
bus.detach(...)
```

以及 JobBoard 自己暴露的必要查询能力。

---

# 54. Book API 不属于 Public Surface

Base Book 可以存在：

```python
book.get()
book.insert()
book.update()
book.delete()
```

这样的内部实现方法。

但这些是：

> **BUS Internal API。**

BUS 外部模块不能直接拿到 Book object。

这是非常重要的权限边界。

---

# 55. 核心数据流

## 普通 Job

```text
Producer
   │
   │ publish
   ▼
JobBoard
   │
   │ claim
   ▼
Consumer
```

Producer 不需要知道 Consumer 是谁。

Consumer 不需要知道 Producer 是谁。

---

# 56. Book Mutation

```text
External Component
        │
        │ publish
        ▼
 ManageBookJobBoard
        │
        │ BUS internal consume
        ▼
       Book
```

外部模块没有：

```text
Component → Book
```

路径。

---

# 57. Publish Hook

```text
                     ┌── Handler A
                     ├── Handler B
Job.publish ─────────┼── Handler C
                     └── Handler N
```

原生支持 MULTI。

---

# 58. Control Hook

```text
Job
 │
 ▼
pre_claim
 │
 │ exactly 0..1 Handler
 ▼
claim
 │
 │ exactly 0..1 Handler
 ▼
post_claim
```

任何 SINGLE Slot 已占用后的再次 attach 都失败。

---

# 59. 外部 Dock

如果需要：

```text
Hook A
Hook B
Hook C
```

同时处理：

```text
pre_claim
```

则：

```text
Hook A ─┐
Hook B ─┼── Dock ─────→ pre_claim
Hook C ─┘
```

Dock 不属于 BUS。

---

# 60. BUS v1 明确实现范围

第一版实现：

- `bus.base`；
- Backend interface；
- File Backend；
- SQLite Backend；
- PostgreSQL Backend interface / implementation；
- Book；
- Job；
- JobBoard；
- ManageBookJobBoard；
- Job Status；
- Job Slots；
- SINGLE / MULTI；
- attach / detach；
- Firmware Books；
- Firmware Jobs；
- Firmware Version；
- Alembic Migration。

---

# 61. BUS v1 明确不实现

不实现：

- Audit System；
- Event Sourcing；
- correlation ID；
- causation ID；
- tracing；
- Book Revision Framework；
- View Layer；
- API Layer；
- Schema Framework；
- Slot Registry Domain；
- priority；
- Hook pipeline；
- Dock；
- Plugin Manager；
- Plugin Discovery；
- IPC；
- TCP；
- Heartbeat；
- Lease；
- Distributed Lock；
- Distributed Consensus。

---

# 62. 推荐实现阶段

## Phase 1：Base

首先实现：

```text
Backend
Book
Job
JobBoard
```

确保这些 primitive 足够简单。

---

## Phase 2：Backend

优先完成：

```text
File Backend
SQLite Backend
```

PostgreSQL 可以随后补齐。

其中 SQLite 很适合作为当前 MAGI standalone 默认实现。

---

## Phase 3：ManageBookJobBoard

实现：

```text
Book
   │
   └── ManageBookJobBoard
```

重点验证：

```text
publish EditJob
      ↓
BUS automatic consume
      ↓
Book mutation
      ↓
Job completed
```

并确保外部没有直接修改 Book 的路径。

---

## Phase 4：Firmware

选择一个最简单的现有 Domain。

例如某个基础 Registry。

建立：

```text
Concrete Book
Concrete ManageBookJob
Concrete JobBoard
```

验证整个模式。

---

## Phase 5：Slot

给 Base Job 增加：

```text
pre_publish
publish
post_publish
pre_claim
claim
post_claim
```

实现：

```text
SINGLE
MULTI
attach
detach
```

---

## Phase 6：迁移 MAGI 模块

逐步将：

```text
Agent → Tools
Tools → Agent
Plugin → DB
Module → DB
```

改成：

```text
Component → BUS
```

不要一次性全部重写。

---

# 63. 核心测试

## Backend

同一套 Backend Contract Test 应该运行在：

```text
File
SQLite
PostgreSQL
```

确保它们对 Base 表现一致。

---

## Book

测试：

- insert；
- get；
- update；
- delete；
- query；

这些测试属于 BUS 内部。

同时 Architecture Test 要确保外部模块不能直接 import / acquire Book。

---

## ManageBookJob

测试：

```text
publish
→ automatic execute
→ Book changed
→ Job COMPLETED
```

失败：

```text
publish
→ mutation failed
→ Job FAILED
→ Book remains valid
```

---

## JobBoard

测试：

```text
publish
claim
complete
fail
```

以及非法 state transition。

---

## SINGLE Slot

```text
attach A
→ OK

attach B
→ SlotOccupiedError

detach A

attach B
→ OK
```

---

## MULTI Publish Slot

```text
attach A
attach B
attach C

publish

A receives
B receives
C receives
```

---

# 64. 架构硬性不变量

以下规则应该作为新版 MAGI-BUS 的 Hard Invariants。

### 1.

**模块之间通过 BUS 解耦，不直接相互依赖。**

### 2.

**BUS 外部模块不能直接访问 Book。**

### 3.

**BUS 外部模块不能直接访问数据库 Backend。**

### 4.

**Book 的外部 Mutation 必须通过对应 ManageBookJobBoard。**

### 5.

**ManageBookJob 不需要外部 claim，由 BUS 自动执行。**

### 6.

**Job History 本身承担操作记录，不额外建立 Audit System。**

### 7.

**Base 只定义通用机制，不包含 MAGI Domain。**

### 8.

**Firmware 定义具体 Book 与 Job。**

### 9.

**Slot 是 Job 的 Feature。**

### 10.

**Publish Slot 为 MULTI。**

### 11.

**Pre/Post/Claim Slot 为 SINGLE。**

### 12.

**SINGLE Slot 已占用时，新的 attach 必须被拒绝。**

### 13.

**BUS 不处理 SINGLE Slot 中的多 Handler composition。**

### 14.

**BUS 不提供 Hook Priority。**

### 15.

**多 Handler composition 由 BUS 外部 Dock 解决。**

### 16.

**BUS 不管理 Plugin。**

### 17.

**Launcher 管理组件组合，但不改变 BUS Firmware 语义。**

### 18.

**当前进程模型不是 BUS Contract 的组成部分。**

---

# 65. 最终核心模型

经过当前设计收敛以后，MAGI-BUS 实际上只剩下三个最核心的业务抽象：

```text
Book
    保存当前状态

Job
    表示系统中发生或需要发生的行为

Firmware
    定义当前 MAGI 具体有哪些 Book 和 Job
```

而：

```text
Slot
```

是 Job 的生命周期能力。

```text
Backend
```

则是这些数据的持久化机制。

最终关系：

```text
                   Firmware
                      │
            ┌─────────┴─────────┐
            │                   │
          Books                Jobs
                                │
                           Job Slots
            │                   │
            └─────────┬─────────┘
                      │
                   BUS Base
                      │
                   Backend
                      │
         ┌────────────┼────────────┐
         ▼            ▼            ▼
       File         SQLite     PostgreSQL
```

---

# 66. 设计总结

新版 MAGI-BUS 不需要成为一个复杂的分布式事件系统。

也不需要为了未来可能出现的问题提前加入：

```text
Tracing
Audit Framework
Event Sourcing
Revision Framework
View Layer
API Layer
Priority System
Orchestration
```

当前最重要的是建立一个足够简单但足够严格的 Software Backplane：

> **Book 保存状态。**

> **Job 保存行为。**

> **JobBoard 管理 Job。**

> **ManageBookJobBoard 是修改 Book 的唯一外部通道。**

> **Job 自带 Slot，允许外部逻辑在固定生命周期节点接入。**

> **Publish 可以广播，控制 Slot 必须保持单一。**

> **Firmware 定义具体协议，Base 只保证这些机制能够工作。**

> **Backend 决定数据最终存入 File、SQLite 还是 PostgreSQL。**

而 BUS 最重要的边界则可以总结成一句：

> **BUS 负责定义和维护自己的线路；线路外面的组件如何安装、组合、排序和运行，不是 BUS 的事情。**