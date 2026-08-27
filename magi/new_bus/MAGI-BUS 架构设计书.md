# MAGI-BUS 架构设计书

**状态：** v0.1.0 实现基线  
**参考分支：** `v0.1.0`  
**当前部署模型：** 一个 MAGI 一个进程  
**定位：** MAGI 内部的软件总线与协议背板

---

# 1. 设计目标

MAGI-BUS 是单个 MAGI Runtime 内部的共享软件总线。

它不负责实现 Agent、Tools、Channels、Plugins 等具体业务能力，也不负责组织这些模块之间的直接调用关系。BUS 的职责是提供一套稳定、严格、可替换的软件背板协议，使不同组件可以围绕 Book、Job、JobBoard、Slot 与 Firmware 协作，而无需直接依赖彼此。

核心目标是：

> **模块依赖 BUS Firmware，而不是依赖其他模块。**

因此模块关系应当是：

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

BUS 的目标不是消灭耦合，而是把跨模块耦合统一收敛成对 BUS Firmware 协议的依赖。

---

# 2. 设计哲学

MAGI-BUS 的设计借鉴硬件总线。

一个接入总线的模块：

- 知道总线提供什么协议；
- 知道自己需要发布什么 Job、处理什么 Job；
- 不需要知道数据由哪个模块产生；
- 不需要知道自己的输出最终会被哪个模块消费；
- 不应该绕过 BUS 直接访问其他模块的内部状态。

因此：

> **BUS 定义协议与线路，Launcher 负责装配组件，Worker 负责执行具体行为。**

BUS 本身应尽量保持机械、稳定和确定，不演化成 Plugin Manager 或 Workflow Engine。

---

# 3. 当前进程模型

当前版本采用：

> **一个 MAGI = 一个进程。**

典型结构：

```text
┌──────────────────── MAGI Process ────────────────────┐
│                                                     │
│  Launcher                                           │
│     ├── BUS                                         │
│     ├── Agent                                       │
│     ├── Tools                                       │
│     ├── Channels                                    │
│     └── Plugins / Workers                           │
│                                                     │
└─────────────────────────────────────────────────────┘
```

当前 BUS 不要求：

- IPC；
- TCP；
- Unix Socket；
- Named Pipe；
- 独立 BUS 进程；
- 独立 Plugin 进程。

但同进程不意味着模块可以直接耦合。逻辑上仍然应该是：

```text
Component
    │
    ▼
BusForWorker / JobBoardClient
    │
    ▼
   BUS
```

而不是：

```text
Component A ───→ Component B
```

因此：

> **BUS 定义逻辑通信边界，不定义部署边界。**

未来 Launcher 可以改变部署方式，但不应该因此改变 Firmware 中 Book、Job、JobBoard 与 Slot 的语义。

---

# 4. BUS 的职责边界

MAGI-BUS 当前负责：

- Book 基础模型；
- Job 与 JobResult 基础模型；
- JobBoard；
- Book Operation Job；
- Job 生命周期；
- Slot 定义与 ownership；
- BusForWorker 与 JobBoardClient；
- Worker liveness 与 Slot lease；
- Dock routing mechanism；
- SQLite / PostgreSQL 持久化；
- FileBook 基础能力；
- Firmware 加载；
- Firmware Schema Migration。

BUS 不负责：

- Plugin 安装与卸载；
- Plugin discovery；
- Plugin dependency；
- Plugin enable / disable；
- Plugin 的业务优先级；
- Agent / Tool / Channel 业务逻辑；
- Launcher 的组件规划；
- 进程管理；
- 网络通信。

核心边界是：

> **BUS 管理自己的协议、Job 状态和 Slot；它不理解接入 BUS 的组件具体是什么业务模块。**

---

# 5. 总体架构

当前 BUS 可以分为三层：

```text
                    External Workers
                          │
                          ▼
                    BusForWorker
                          │
                          ▼
┌───────────────────────────────────────────────┐
│                    BUS                        │
│                                               │
│      JobBoards / Slots / Docks / Heartbeat    │
└───────────────────────┬───────────────────────┘
                        │
                        ▼
┌───────────────────────────────────────────────┐
│                bus.firmware                   │
│                                               │
│      Concrete Books + Concrete Jobs           │
│          Firmware Schema Versions             │
└───────────────────────┬───────────────────────┘
                        │
                        ▼
┌───────────────────────────────────────────────┐
│                   bus.base                    │
│                                               │
│ BaseBook / BaseJob / BaseJobBoard             │
│ OperateBookJobBoard / Slot / BusForWorker     │
│ Engine / FileBook primitives                  │
└───────────────────────┬───────────────────────┘
                        │
              ┌─────────┴─────────┐
              ▼                   ▼
          SQL Storage         File Storage
        SQLite/PostgreSQL       Directory
```

其中最重要的分工是：

> **Base 定义通用机制。Firmware 定义具体协议。**

---

# 6. `bus.base`

`bus.base` 是 BUS 最稳定、最通用的一层。

Base 不应该知道：

- Agent；
- Tool；
- Channel；
- Conversation；
- Message；
- LLM；
- Telegram；
- MCP。

当前 Base 的主要 primitive 包括：

```text
BaseRecord
BaseBook
BaseFileBook

BaseJob
BaseJobResult
BaseJobRow
BaseJobBoard
OperateBookJobBoard

Slot
Heartbeat

BusForWorker
JobBoardClient

OrDock
AndDock

EngineFactory
SQLiteBackend
PostgresBackend
FileBackend
```

Conversation、Message 等具体 MAGI 语义只能出现在 Firmware。

---

# 7. Book：当前状态

Book 表示：

> **BUS 当前保存的状态。**

当前 Firmware 已经具有：

```text
ConversationBook
MessageBook
```

SQL Book 中，一条稳定的数据记录由 Dataclass 表示，例如：

```text
Conversation
Message
```

同时存在 BUS 内部 ORM Row：

```text
Conversation
      │
      ▼
ConversationRow
      │
      ▼
books_conversations
```

其中：

- Record 表示稳定的数据字段；
- Row 表示 BUS 内部 SQL 持久化结构；
- Book 表示一组 Record/Row 的内部集合。

---

# 8. Book 是 BUS 内部对象

重要不变量：

> **BUS 外部 Worker 不直接读取或修改 Book。**

正常外部组件不应该直接：

```python
ConversationBook(...)
MessageBook(...)
```

也不应该直接：

```python
book.add(...)
book.update(...)
book.delete(...)
book.list(...)
```

虽然 `BaseBook` 内部提供 CRUD 能力，但这些方法属于：

> **BUS Internal API。**

它们供 Firmware 中的语义 Job 实现使用，不属于 Worker Public Surface。

外部组件也不应该直接访问：

- SQLAlchemy Row；
- SQLAlchemy Session；
- Engine；
- 数据库表。

---

# 9. 外部组件通过 Semantic Job 操作 Book

当前实现没有使用一个通用 `EditBookJob` 对外暴露 CRUD。

Firmware 为 Book 定义具备明确业务语义的 Job，例如：

```text
CreateConversationJob
UpdateConversationSummaryJob

AppendMessageJob
ListConversationMessagesJob
ArchiveMessagesJob
```

这意味着外部组件表达的是：

```text
UpdateConversationSummary
```

而不是：

```text
UPDATE conversation SET ...
```

表达的是：

```text
ListConversationMessages
```

而不是：

```text
SELECT * FROM messages ...
```

因此：

> **Firmware 对外暴露语义操作，而不是数据库 CRUD。**

这是当前 BUS 架构的重要原则。

---

# 10. Book Query 同样 Job 化

“外部不触碰 Book”不仅适用于 Mutation，也适用于 Query。

例如：

```text
Worker
   │
   │ publish ListConversationMessagesJob
   ▼
BUS
   │
   │ query MessageRow
   ▼
ListConversationMessagesResult
```

因此 Book 可以保持完全 BUS-private。

外部组件无需获得 Book reference，也无需增加额外 View 层或 API 层。

> **JobBoard API 本身就是 BUS 的行为 API。**

---

# 11. `OperateBookJobBoard`

所有直接操作内部 Book 的 Firmware Job 可以继承：

```text
OperateBookJobBoard
```

与普通 Worker JobBoard 不同，OperateBookJobBoard：

- 接收正常 publish；
- 不允许外部 claim；
- Job 进入可执行状态后由 BUS 自己执行；
- Book 操作与 Job terminal result 在同一事务中提交。

基础路径：

```text
publish
   │
   ▼
PENDING
   │
   ▼
EXECUTING
   │
   ├────→ COMPLETED
   │
   └────→ FAILED
```

真正的数据修改始终发生在 BUS/Firmware 内部。

---

# 12. Job History 即 Audit Trail

当前 BUS 不建立额外：

```text
Audit
AuditRecord
MutationProvenance
EventLog
```

系统。

原因是 Job 本身已经持久化：

- publisher；
- created_at；
- 输入字段；
- lifecycle status；
- result；
- error。

因此对于一个 Book Operation：

```text
Job
 ├── Request
 ├── Status
 ├── Result Snapshot
 └── Error
```

本身已经构成完整的操作历史。

> **Job History 就是 BUS 的 Audit Trail。**

不需要再维护第二套 Audit 数据。

---

# 13. Job 基础模型

`BaseJob` 表示：

> **某件需要发生、正在发生或已经发生的事情。**

当前 BaseJob 的基础字段保持轻量：

```text
id
created_at
updated_at
publisher
```

具体业务字段由 Firmware Job Dataclass 定义。

例如：

```text
AppendMessageJob
├── conversation_id
├── role
├── content
└── timestamp
```

当前 BUS 不强制加入：

```text
correlation_id
causation_id
trace_id
span_id
revision
mutation_provenance
```

等复杂事件系统字段。

只有产生明确实际需求以后才增加。

---

# 14. Job Result

Job 的请求和结果使用不同类型：

```text
BaseJob
BaseJobResult
```

Firmware 可以进一步定义：

```text
CreateConversationJob
CreateConversationResult

AppendMessageJob
AppendMessageResult
```

Request 与 Result 使用同一 Job Row 持久化。

因此一条 Job Row 同时承担：

```text
Request
+
Lifecycle State
+
Result
```

这避免再增加 Result Table 或 Audit Table。

---

# 15. Job 状态机

当前 `JobStatus` 包含：

```text
PREPARING
HOOKING
PENDING
CLAIMED
EXECUTING
SETTLING
FINALIZING
COMPLETED
FAILED
```

不同 Job 类型和不同 Slot 配置并不一定经过所有状态。

状态机根据实际是否存在 Hook 自动缩短。

---

# 16. 普通 Worker Job 生命周期

普通 Job 在没有额外 Hook 时：

```text
publish
   │
   ▼
PENDING
   │
   ▼
claim
   │
   ▼
CLAIMED
   │
   ▼
submit_result
   │
   ├────→ COMPLETED
   │
   └────→ FAILED
```

`claim` 使用原子状态更新：

```text
PENDING → CLAIMED
```

只有成功完成状态转换的 Worker 可以取得该 Job，避免一个 Job 被多个 Worker 同时 claim。

---

# 17. Post-Publish Gate

当前实现没有把 Job lifecycle 建模成抽象的 `pre_publish / pre_claim / post_claim`。

实际实现采用明确的 pull/submit Gate：

```text
post_publish
submit_post_publish
```

如果没有 Worker 占据 `post_publish`：

```text
publish
   │
   ▼
PENDING
```

Job 可以立即进入可执行状态。

如果存在 `post_publish` Handler：

```text
publish
   │
   ▼
PREPARING
   │
   ▼
post_publish()
   │
   ▼
HOOKING
   │
   ▼
submit_post_publish()
   │
   ├──── approve ──→ PENDING
   │
   └──── reject ───→ FAILED
```

因此 `post_publish` 实际表示：

> **Job 已经被记录，但在进入可执行队列之前进行检查。**

例如 Policy 或 Security Worker 可以在这里阻止某个 Job 真正被执行。

---

# 18. Post-Result Gate

Worker Result 同样支持一个可选 Gate：

```text
post_result
submit_post_result
```

如果不存在 post-result Hook：

```text
submit_result
   │
   ├────→ COMPLETED
   └────→ FAILED
```

如果存在：

```text
submit_result
   │
   ▼
SETTLING
   │
   ▼
post_result()
   │
   ▼
FINALIZING
   │
   ▼
submit_post_result()
   │
   ├────→ COMPLETED
   └────→ FAILED
```

因此外部 Hook 可以在 Worker 提交结果后、结果正式终结前进行最终处理。

---

# 19. OperateBookJob 与 Gate

Book Operation Job 同样可以经过 `post_publish` Gate。

例如：

```text
CreateConversationJob
       │
       ▼
    publish
       │
       ▼
   PREPARING
       │
       ▼
 post_publish checker
       │
       ├── FAILED
       │
       └── PENDING
              │
              ▼
       BUS executes Book mutation
```

因此外部 Policy Worker 可以阻止 Book Operation 真正发生，而仍然不需要接触 Book。

---

# 20. Slot 是 JobBoard Operation 的 Feature

Slot 不是 Firmware 中独立于 Job 的 Domain。

当前 Slot 的身份由：

```text
(JobType, OperationName)
```

组成。

例如：

```text
Slot(PingJob, "publish")
Slot(PingJob, "claim")
Slot(PingJob, "post_publish")
Slot(PingJob, "submit_post_result")
```

JobBoard 使用 `@slot` 标记一个 operation 是否属于可被 Worker 占用的 Slot。

因此：

> **Slot 是 Job 生命周期/JobBoard operation 上允许外部 Worker 接入的位置。**

不需要单独的 `firmware/slots/` 模块。

---

# 21. 当前 Slot Operations

`BaseJobBoard` 当前暴露的 Slot operation 包括：

```text
publish

post_publish
submit_post_publish

claim
submit_result

post_result
submit_post_result
```

而以下接口不是 Slot：

```text
get_result
check_job_status
list
```

它们属于 JobBoard 的普通查询能力。

---

# 22. 统一单 Owner Slot 模型

当前实现选择：

> **所有原始 Slot 都采用统一的单 owner 模型。**

即：

```text
Slot
 │
 └── 0..1 owner
```

如果 Worker A 已经直接拥有：

```text
Slot(Job, "claim")
```

Worker B 不能直接获得同一个 Slot。

BUS 不根据以下信息进行仲裁：

- Plugin priority；
- load order；
- Plugin version；
- random selection。

直接 Slot 冲突会被拒绝。

这种统一模型避免 BUS Core 同时维护 SINGLE/MULTI 两套 Slot ownership 语义。

---

# 23. Publish 也使用统一 Slot 模型

早期设计曾考虑：

```text
publish = MULTI
control slots = SINGLE
```

当前实现最终选择：

```text
all raw slots = single owner
```

因此多个 Publisher 不直接同时拥有 `publish` Slot，而是：

```text
Publisher A ─┐
Publisher B ─┼── OrDock ─── publish Slot
Publisher C ─┘
```

从 Worker 使用效果来看仍然可以有多个 Publisher，但 BUS Core 不需要为 publish 建立特殊 cardinality。

> **多 Worker 共享问题统一交给 Dock。**

---

# 24. Heartbeat 与 Slot Lease

当前 Slot ownership 由 BUS-private `Heartbeat` 管理。

Heartbeat 保存：

```text
worker_id → lease expiration
Slot      → owner
```

Worker attach 成功后获得 Slot，之后在调用 Slot 或主动 heartbeat 时刷新 lease。

当前 lease 很短，其目的不是做网络健康检查，而是：

> **防止一个已经停止活动的 Worker 永久占据 Slot。**

如果 Worker lease 过期：

```text
Worker expires
      │
      ▼
Slot ownership released
```

其他 Worker 可以重新 attach。

因此 Heartbeat 是当前单进程 Runtime 中的 Slot liveness/ownership 机制，并不意味着 BUS 必须采用多进程部署。

---

# 25. Hook 消失后的 Job 自动释放

Heartbeat 还用于避免失效 Hook 永久卡住 Job。

例如 Job 当前处于：

```text
PREPARING / HOOKING
```

等待 post-publish Handler。

如果对应 Slot 已经没有 live owner，JobBoard 可以自动将其释放为：

```text
PENDING
```

同样，如果 Result 处于：

```text
SETTLING / FINALIZING
```

而 post-result Hook 已经不存在，则 JobBoard 可以根据已有 result/error 自动进入：

```text
COMPLETED
/
FAILED
```

因此一个失效 Hook 不会永久冻结 Job 生命周期。

---

# 26. BusForWorker

外部 Worker 不直接获得原始 Bus、Book 或 JobBoard 内部实现。Worker 是由
`RuntimeLauncher` 创建的业务组件；BUS 在为它分配声明的 Slot 后，才交给它
一个访问切面：

```text
BusForWorker
```

例如，Launcher 先创建 Worker，再分配切面并调用其 `attach`：

```python
worker = ProviderWorker()
bus_for_worker = bus.for_worker("provider-a", provider_slots)
worker.attach(bus_for_worker)
```

BusForWorker 是：

> **绑定到具体 Worker identity、并受已分配 Slot 约束的 BUS access slice。**

它不是第二个 BUS；所有 Worker 切面仍指向同一个 Runtime BUS。切面通过
`board(JobType)` 取得 JobBoardClient，而每次写操作仍由共享 BUS 检查 Slot
ownership。

---

# 27. WorkerLaunchSpec

Worker topology 不再由 Bus view 子类隐式声明。`WorkerLaunchSpec` 显式声明：

- `worker_id`；
- `slots`；
- `create`（Worker 构造器）。

例如：

```python
WorkerLaunchSpec(
    worker_id="tools-a",
    slots=(
        Slot(ToolCallJob, "claim"),
        Slot(ToolCallJob, "submit_result"),
    ),
    create=ToolWorker,
)
```

RuntimeLauncher 在创建 BUS 后收集这些 Slot，先规划 Dock，再调用
`bus.for_worker(worker_id, slots)` 分配切面。Worker 只在自己的 `attach()`
中接收该切面。

---

# 28. JobBoardClient

Worker 实际拿到的是：

```text
JobBoardClient
```

而不是原始 `BaseJobBoard`。

JobBoardClient：

- 自动携带 worker_id；
- 将 Slot operation 通过 BUS `_invoke()` 调用；
- 不能绕过 Slot ownership；
- 暴露 typed Job / Result API。

因此调用方式可以保持接近普通对象调用：

```python
job_id = worker_bus.messages.publish(job)
job = worker_bus.messages.claim()
worker_bus.messages.submit_result(result)
```

虽然当前所有组件都在同一进程，这种 facade 仍然强制保持 BUS 逻辑边界。

---

# 29. Dock

当前 Base 提供两个通用 Dock：

```text
OrDock
AndDock
```

Dock 的核心作用是：

> **让多个 Worker 在外部组成一个逻辑 Slot owner。**

BUS 内真正拥有原始 Slot 的是 Dock，Worker 成为 Dock 的 member。

因此原始 Slot 仍然维持单 owner 不变量。

---

# 30. OrDock

`OrDock` 的语义是：

> **任意一个 live member 都可以使用这个 Slot。**

例如：

```text
Worker A ─┐
Worker B ─┼── OrDock ─── Slot(Job, "publish")
Worker C ─┘
```

BUS Slot 本身仍然只有一个 owner：

```text
OrDock
```

但多个 Worker 都可以通过 Dock 调用该 operation。

适合：

- publish；
- claim；
- submit_result；
- 其他任意一个成员即可完成的 Slot。

---

# 31. AndDock

`AndDock` 用于：

> **多个 Worker 都需要对同一次提交给出结果。**

例如：

```text
Worker A ─┐
Worker B ─┼── AndDock ─── submit_post_result
Worker C ─┘
```

AndDock 为同一个 Job 收集当前 live member 的 vote/result。

当前 reducer 规则保持简单：

```text
任意一个 FAILED
        │
        ▼
整体 FAILED
```

否则采用成功结果继续提交。

更复杂的业务级 reducer 可以未来扩展，但不属于当前 BUS 核心协议。

---

# 32. Dock 与 BUS / Launcher 的边界

Dock 的通用 mechanism 位于 BUS Base 中，但：

> **BUS 不主动决定什么时候应该使用 Dock。**

决定权属于 Launcher。

边界是：

```text
BUS:
    提供 OrDock / AndDock mechanism
    保证 Slot ownership
    提供 routing

Launcher:
    查看 Worker 声明
    规划 topology
    决定是否安装 Dock
    选择 Dock 类型
```

因此 Dock mechanism 属于 BUS 能力，而 Dock topology / composition policy 属于 Launcher。

BUS 不需要理解 Plugin topology。

---

# 33. Launcher

当前 `magi/launcher/runtime_launcher.py` 中的 `RuntimeLauncher` 负责在 Worker 启动前规划 Slot topology。

Launcher 的基本流程：

1. 收集所有 Worker 声明的 Slot；
2. 统计同一个 Slot 有多少 Worker 请求；
3. 单 Worker Slot 直接 attach；
4. 多 Worker Slot 安装对应 Dock；
5. 创建 Worker；
6. 通过 `bus.for_worker(worker_id, slots)` 分配 BusForWorker；
7. 调用 `worker.attach(bus_for_worker)`。

概念上：

```text
RuntimeLauncher
   │
   ├── create Bus
   ├── plan topology
   ├── install Docks
   ├── create Workers
   ├── allocate BusForWorker slices
   └── attach Workers
```

BUS 本身不搜索 Plugin，也不决定哪些 Worker 应该存在。

---

# 34. Launcher 当前默认 Dock Policy

当多个 Worker 请求同一个 Slot 时，当前 Launcher 默认：

```text
submit_post_publish
submit_post_result
        │
        ▼
      AndDock
```

其他重复 Slot：

```text
publish
claim
submit_result
...
        │
        ▼
      OrDock
```

这是：

> **Launcher policy。**

不是 Firmware 的业务协议，也不是 Job schema 的组成部分。

未来其他 Launcher 可以采用不同组合策略。

---

# 35. Backend 总体模型

当前 BUS 存在两条不同持久化路径。

SQL Book / Job：

```text
BaseBook / BaseJobBoard
        │
        ▼
   EngineFactory
        │
   ┌────┴────┐
   ▼         ▼
SQLite   PostgreSQL
```

File Book：

```text
BaseFileBook
     │
     ▼
 FileBackend
     │
     ▼
Directory
```

这两条路径当前并不是一个“所有功能完全可互换”的统一 Backend interface。

---

# 36. SQLite Backend

`SQLiteBackend` 基于 `EngineFactory`。

它是当前最适合本地 MAGI 使用的 SQL persistence。

当前实现包括：

- SQLAlchemy Engine；
- Session；
- Foreign Key；
- WAL；
- busy timeout；
- Transaction。

SQLite 支持 memory mode，但：

> **Memory 不是一个独立的正式 Backend 类型。**

测试中的 `InMemoryBackend` 位于 `tests`，不属于生产 BUS API。

---

# 37. PostgreSQL Backend

`PostgresBackend` 同样基于 `EngineFactory`。

因此 SQL Book / JobBoard 上层代码不需要关心底层是：

```text
SQLite
```

还是：

```text
PostgreSQL
```

这构成当前 SQL storage abstraction 的主要边界。

---

# 38. File Backend

`FileBackend` 当前不是 `EngineFactory` 的实现。

它提供一个文件根目录给：

```text
BaseFileBook
```

使用。

BaseFileBook 表示：

> **由一组具名文件构成的 Book。**

基础能力包括：

```text
read
write
exists
iterate
```

因此：

```text
BaseBook
BaseFileBook
```

是两种平行 primitive。

当前 FileBackend 不是完整 JobBoard persistence 的替代品。

---

# 39. 当前 Backend 定位

因此当前架构应准确理解为：

```text
SQL BUS persistence:
    SQLite
    PostgreSQL

File Book persistence:
    FileBackend
```

而不是：

```text
File / SQLite / PostgreSQL
三种完全可互换的整个 BUS Runtime Backend
```

如果未来需要纯文件运行完整 BUS，需要另外实现 File-based Job persistence。

---

# 40. Firmware

Firmware 定义：

> **这一代 MAGI-BUS 具体有哪些 Book、Job、JobResult 和数据库结构。**

当前 Firmware 包含：

```text
Books
├── ConversationBook
└── MessageBook

Jobs
├── CreateConversationJob
├── UpdateConversationSummaryJob
├── AppendMessageJob
├── ListConversationMessagesJob
└── ArchiveMessagesJob
```

Base 完全不知道这些具体业务概念。

---

# 41. Firmware 自动加载

创建：

```python
Bus(factory)
```

时，BUS 会自动：

```text
prepare Firmware schema
        │
        ▼
create Firmware JobBoards
        │
        ▼
mount into BUS runtime
```

调用方不需要逐个 mount Book / Job / JobBoard。

当前 Firmware 是随该版本 BUS 一起发布的一组固定协议。

---

# 42. Firmware Schema 与 Version

当前 Firmware 不建立额外：

```text
schemas/
```

系统。

SQLAlchemy Row 定义就是当前 schema source。

Schema evolution 使用 Alembic 管理：

```text
firmware/
└── versions/
    ├── 0.0.1.py
    ├── env.py
    └── schema.py
```

Alembic revision 使用：

```text
0.0.1
```

形式。

因此当前设计选择：

> **数据库 Schema evolution 与 Firmware version history 统一由 Firmware Alembic versions 管理。**

不再额外维护独立 Schema 版本系统。

---

# 43. Firmware Compatibility

当前实现已经具有 Firmware schema revision，但 Worker / Plugin 的最低 Firmware compatibility 声明仍可以继续完善。

目标可以是：

```text
Worker / Plugin
    requires Firmware >= X
             │
             ▼
Launcher / BUS compatibility check
```

版本来源可以直接基于 Firmware 的版本体系，不需要再创造第二套复杂版本对象。

兼容策略属于协议演进问题，不影响当前 Book / Job / Slot 基本结构。

---

# 44. Public Surface

当前推荐的外部使用路径是：

```text
Bus
 │
 ▼
BusForWorker
 │
 ▼
JobBoardClient
 │
 ▼
Firmware Job / Result
```

而不是：

```text
External Worker
     │
     ├── BaseBook
     ├── SQLAlchemy Session
     ├── Row
     └── Engine
```

这保证外部组件即使运行在同一个 Python 进程中，也只能通过逻辑 BUS Contract 工作。

---

# 45. 核心数据流

## 45.1 普通 Worker Job

```text
Producer
   │
   │ publish
   ▼
JobBoard
   │
   │ claim
   ▼
Worker
   │
   │ submit_result
   ▼
JobBoard
```

Producer 不需要知道最终 Worker 是谁。

Worker 也不需要知道 Job 由哪个业务模块产生。

---

## 45.2 Book Operation

```text
External Worker
      │
      │ publish semantic job
      ▼
OperateBookJobBoard
      │
      │ internal execution
      ▼
     Book
      │
      ▼
 Job Result
```

外部 Worker 从始至终不接触 Book。

---

## 45.3 带 Post-Publish Gate 的 Book Operation

```text
External Worker
      │
      ▼
    publish
      │
      ▼
  PREPARING
      │
      ▼
 post_publish
      │
      ├── FAILED
      │
      └── PENDING
             │
             ▼
      BUS executes Book
```

---

# 46. 当前目录结构

当前实现主要结构：

```text
magi/
├── new_bus/
│   ├── __init__.py
│   ├── bus.py
│   ├── bus_for_worker.py
│   │
│   ├── base/
│   │   ├── BaseBook.py
│   │   ├── BaseFileBook.py
│   │   ├── BaseJob.py
│   │   ├── operateBookJob.py
│   │   ├── heartbeat.py
│   │   ├── dock.py
│   │   ├── engine.py
│   │   ├── file.py
│   │   ├── errors.py
│   │   └── time.py
│   │
│   └── firmware/
│       ├── __init__.py
│       ├── books/
│       │   ├── conversationBook.py
│       │   └── messageBook.py
│       ├── jobs/
│       │   ├── conversationJobs.py
│       │   └── messageJobs.py
│       └── versions/
│           ├── 0.0.1.py
│           ├── env.py
│           └── schema.py
│
└── launcher/
    └── runtime_launcher.py
```

目录名未来可以随着 `new_bus` 正式替代旧 BUS 而调整，但逻辑分层保持不变。

---

# 47. 当前核心不变量

以下规则应作为当前 MAGI-BUS 的 Hard Invariants。

1. **模块之间通过 BUS 协议解耦，不直接依赖其他业务模块。**

2. **BUS 外部 Worker 不直接读取或修改 Book。**

3. **BUS 外部 Worker 不直接访问 SQLAlchemy Row、Session 或 Engine。**

4. **对共享 Book 的外部操作通过 Firmware Semantic Job 表达，而不是暴露通用 CRUD。**

5. **Book Query 同样可以通过 Semantic Job 表达，从而维持 Book 完全 BUS-private。**

6. **OperateBookJob 由 BUS 内部执行，不允许外部 claim。**

7. **Book Operation 与 Job Result 保持事务一致性。**

8. **Job History 本身承担操作历史，不额外建立 Audit subsystem。**

9. **Base 不包含 MAGI Domain 概念。**

10. **Firmware 定义具体 Book、Job 与 Result。**

11. **Slot 是 `(JobType, JobBoard Operation)` 的运行时接入点。**

12. **所有原始 Slot 当前统一采用单 owner 模型。**

13. **多个 Worker 共享 Slot 时通过 Dock 形成一个逻辑 owner。**

14. **BUS 不根据 Plugin priority、加载顺序等策略自动决定 Slot ownership。**

15. **Dock mechanism 属于 BUS Base；Dock topology 与选择策略属于 Launcher。**

16. **Heartbeat lease 是 Slot liveness/ownership 机制，不代表 BUS 必须多进程运行。**

17. **当前部署方式不是 Firmware Contract 的组成部分。**

---

# 48. 测试原则

当前 `tests/unit/new_bus` 已经覆盖了一批关键架构性质，后续应持续保证：

```text
SQLite Backend works

InMemory test backend
    != production backend

Book Operation Job
    cannot be externally claimed

Book Operation failure
    persists FAILED Job Result

Slot conflict
    rejects second direct owner

Expired Slot owner
    can be replaced

Post-Publish Hook
    can approve or reject Job

Post-Result Hook
    can approve or reject Result

OrDock
    allows multiple Workers to share one logical Slot

AndDock
    waits for live member results

Launcher
    installs Docks before Worker attach
```

随着 Firmware 增加新 Book / Job，应优先测试协议行为和状态机，而不是只测试具体 SQL 实现。

---

# 49. 架构决策总结

当前实现已经把早期概念方案进一步收敛成几个明确设计决定。

## 49.1 Semantic Operation 优先于 Generic CRUD

不是：

```text
EditBookJob(operation="update", ...)
```

而是：

```text
CreateConversationJob
UpdateConversationSummaryJob
AppendMessageJob
ArchiveMessagesJob
```

Firmware 对外表达业务语义，不重新暴露数据库接口。

## 49.2 Query 与 Mutation 都通过 Job

外部组件无需 Book View/API 层。

```text
Worker → JobBoard → Firmware Job → Book
```

已经构成完整边界。

## 49.3 所有 Slot 使用统一单 Owner 模型

BUS Core 不区分 publish MULTI 与 control SINGLE。

多 Worker 共享统一通过 Dock 处理，使 Slot ownership 规则保持单一。

## 49.4 Hook 使用显式 Gate，而不是抽象 Pre/Post 列表

当前协议采用：

```text
post_publish / submit_post_publish
post_result  / submit_post_result
```

通过显式状态转换形成可观察、可恢复的 Gate。

## 49.5 Heartbeat 服务于 Runtime ownership

Heartbeat 不是为了提前设计分布式系统，而是解决当前 Slot owner 失活以后如何自动释放的问题。

---

# 50. 设计总结

当前 MAGI-BUS 的核心可以概括为：

> **Book 保存当前状态。**

> **Job 表达系统中需要发生、正在发生或已经发生的语义行为。**

> **JobBoard 持久化 Job 并管理其生命周期。**

> **OperateBookJobBoard 将外部 Book 操作转化为 BUS 内部事务。**

> **Job History 本身承担操作历史。**

> **Slot 定义 Worker 可以接入 JobBoard 生命周期的位置。**

> **所有原始 Slot 使用统一单 owner 模型，多 Worker 共享由 Dock 实现。**

> **BusForWorker 为 Worker 提供受 Slot ownership 约束的 BUS access slice。**

> **Firmware 定义当前 MAGI 实际拥有的 Book、Job、Result 与数据库版本。**

> **Launcher 决定 Worker 如何装配和共享 Slot，但不改变 Firmware 的业务协议。**

最终希望形成的不是一个越来越聪明的中央 Orchestrator，而是一块行为稳定、协议明确的软件背板：

```text
External Components
        │
        ▼
   BusForWorker
        │
        ▼
       BUS
        │
   ┌────┴────┐
   ▼         ▼
 Jobs       Books
   │
   ▼
Firmware
```

模块可以被替换，Worker 可以重新组合，Launcher 可以改变拓扑，但其他组件只需要继续遵守同一套 Firmware 协议。

这就是 MAGI-BUS 当前架构所追求的模块化边界。
