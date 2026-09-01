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
- Slot 定义与 membership；
- BusForWorker 与 JobBoardClient；
- Worker liveness 与 Slot lease；
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
│        JobBoards / Slots / Heartbeat          │
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

EngineFactory
SQLiteBackend
PostgresBackend
FileEngine
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
ContactBook
ContactNoteBook
SettingsBook
TokenUsageBook
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

## Contact 与 Conversation 的身份边界

`Contact` 表示一个与 Runtime 有关系的、**不依赖 channel 的参与者**：
人、MAGI 或第三方 Agent。它保存名称、展示名称、角色和最近活跃时间，
不保存 Telegram ID、Discord ID、邮箱等 transport identity。

Channel 和投递目标属于 `Conversation`：

```text
Contact (who)
    │
    ▼
Conversation (which interaction)
    ├── owner_contact_id   private context / ownership
    ├── channel            e.g. telegram / discord / webui
    └── delivery_address   channel-specific endpoint

ConvMembersBook
    └── additional current Contact participants for a group Conversation
```

这让同一个 Contact 可以经由不同 channel 与 MAGI 交互，而不用为每一种
channel 给 `Contact` 增加字段。常规模式是 MAGI 与一个 Contact 维持一个
长期 Conversation；需要事务性协作时，创建包含该用户和 MAGI 的独立群聊，
并以该群的 channel / delivery address 创建独立 Conversation。`owner_contact_id`
定义会话归属；其余当前成员存入 `ConvMembersBook`。成员退出时直接删除对应
记录，不保留额外的角色或离开时间字段。

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

## 12.1 Failure Transport

MAGI 是 Agent-first 系统：大多数行为由 chat 链条驱动，少量行为由前端发起。
两条链都不应依赖跨层抛异常来传递可预期的失败。

- Firmware Job 的业务校验、Book 操作、文件或数据库等外部系统失败，必须转换为该 Job 的 `FAILED` result；`error` 保存可向调用方转发的人类可读文本。
- Chat 链应由 Agent 将该 result 继续写成 conversation 中可见的 Message；前端链应将同一错误文本交给前端显示。
- 已持久化 Job 的失败不能只写日志：Job Row 的请求、状态、结果和 `error` 就是唯一审计记录。
- 任何无法沿当前调用链正常返回的失败（包括 Worker 取消）都不能只重新抛出或只写日志：必须写入发起 Job 的 `FAILED` result，或发布 `ChatNotify` / `DeliveryNotify` 形成可见 Message。Chat turn 同时写入失败 result 和 Delivery；两者都是持久化记录。
- `BusForWorker` / `JobBoardClient` 是 BUS 对 Worker 的公开边界。未挂载 Job、未持有 Slot、查询不到记录或基础设施暂时不可用时，返回该操作的正常空值（`None` / `False` / `0` / `[]`），不向 Worker 抛 BUS 异常。
- Python 异常只留在 BUS 内部实现边界；Worker 不按异常类别决定业务失败路径，而是把失败转换为上述 BUS result 或可见 Message。

若 Job Row 本身无法写入（例如数据库完全不可用），BUS 无法凭空持久化一条失败记录；公开调用仍返回空值。恢复后由上层重试或重新投递。

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
PENDING
CLAIMED
EXECUTING
SETTLING
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
claim_post_publish
submit_post_publish
```

如果没有 Worker attach `claim_post_publish`：

```text
publish
   │
   ▼
PENDING
```

Job 可以立即进入可执行状态。

如果存在 post-publish Worker：

```text
publish
   │
   ▼
PREPARING
   │
   ▼
claim_post_publish()
   │
   ▼
PREPARING（可被所有 claim-post-publish Worker 读取）
   │
   ▼
submit_post_publish()（所有 live submitter 都提交）
   │
   ├──── approve ──→ PENDING
   │
   └──── reject ───→ FAILED
```

因此 `claim_post_publish` 实际表示：

> **Job 已经被记录，但在进入可执行队列之前进行检查。**

例如 Policy 或 Security Worker 可以在这里阻止某个 Job 真正被执行。

---

# 18. Post-Result Gate

Worker Result 同样支持一个可选 Gate：

```text
claim_post_result
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
claim_post_result()
   │
   ▼
SETTLING（可被所有 claim-post-result Worker 读取）
   │
   ▼
submit_post_result()（所有 live submitter 都提交）
   │
   ├────→ COMPLETED
   └────→ FAILED
```

因此外部 Hook 可以在 Worker 提交结果后、结果正式终结前进行最终处理。

---

# 19. OperateBookJob 与 Gate

Book Operation Job 同样可以经过 `claim_post_publish` Gate。

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
claim_post_publish checker
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

Worker 声明的是 `SlotTag`；它的身份由：

```text
(JobType, OperationName)
```

组成。

例如：

```text
SlotTag(PingJob, "publish")
SlotTag(PingJob, "claim")
SlotTag(PingJob, "claim_post_publish")
SlotTag(PingJob, "submit_post_result")
```

`slot.py` 中的模块级 `slots` 为每个 `(JobBoard, SlotTag)` 保存唯一的运行时
`Slot`。它保存成员、心跳触达，以及需要时的 per-Worker JobId cursor 和
post-gate 缓存。JobBoard 只声明 operation 的类型，例如：

```python
@slot(
    SlotType.CLAIM_POST,
    pass_if_no_worker=pass_claim_post_publish,
)
def claim_post_publish(...): ...
```

装饰器通过全局 `slots` 找到这个函数对应的运行时实例并执行；JobBoard 不持有
Slot runtime，也不维护 cursor 或投票缓存。`next_slot` 是同一 JobBoard 的下一
operation 名；没有下一阶段时省略它。若下一阶段没有 worker，runtime 调用可选的
`pass_if_no_worker(board, job_id)`。这个 callback 属于下一阶段 Slot 自己；例如
`claim_post_publish` 没有 worker 时，`pass_claim_post_publish` 将 Job 从
`PREPARING` 推进到 `PENDING`。

因此：

> **Slot 是 Job 生命周期/JobBoard operation 上允许外部 Worker 接入的位置。**

不需要单独的 `firmware/slots/` 模块。

---

# 21. 当前 Slot Operations

`BaseJobBoard` 当前暴露的 Slot operation 包括：

```text
publish

claim_post_publish
submit_post_publish

claim
submit_result

claim_post_result
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

# 22. Slot 是多 Worker Membership

一个 Slot 可以由任意多个 Worker attach；BUS 不再为重复 Slot 仲裁 owner，也没有 Dock。

```text
Slot(Job, "publish") ── 0..n live Workers
```

因此所有 attach `publish` 的 Worker 都可以直接入队；所有 attach `claim` 的
Worker 都可尝试原子 `PENDING → CLAIMED`，但同一个 Job 仍然只有一个成功的
claimant。

---

# 23. Heartbeat 与 Slot Lease

BUS-private `Heartbeat` 保存：

```text
worker_id → lease expiration
```

Worker 调用 Slot 或主动 heartbeat 会刷新自己的 lease。运行时 Slot 在访问时
剔除已过期 Worker，不会影响仍存活的其他成员。

---

# 24. Post Gate 的 all-members 提交

`claim_post_publish` 与 `claim_post_result` 不改变 JobStatus；每个 attach 的
Worker 都可以读取同一个 PREPARING 或 SETTLING Job。对应的 `submit_post_*`
在第一次提交时快照当前 live submitter，并收集每个 Worker 的一份结果。

```text
所有预期 Worker 都提交 → 结算
任意一个 FAILED        → FAILED，合并 error
全部非 FAILED          → PENDING / COMPLETED
```

过期 Worker 会从尚未结算的预期集合移除；若整个 claim-post Slot 已无人存活，
JobBoard 会直接释放 Gate，避免 Job 永久卡住。

---

# 25. `submit_result` 是 first-result-wins

`claim` 后第一个有效 `submit_result` 写入最终结果（或进入 post-result Gate）。
后续提交被忽略，既不覆盖第一个结果，也不会重新触发 post-result Gate。

---

# 26. BusForWorker

外部 Worker 不直接获得原始 Bus、Book 或 JobBoard 内部实现。Worker 是由
`Launcher` 创建的业务组件；BUS 在为它分配声明的 Slot 后，才交给它
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

# 27. requiredSlots

Worker topology 由 Worker 包自己的 `requiredSlots.py` 声明，而不是由
Launcher 手写 Slot 列表。调用方把 Worker 类交给控制面板的 `launch`：

```python
# magi/tools/requiredSlots.py
REQUIRED_SLOTS = (
    SlotTag(ToolCallJob, "claim"),
    SlotTag(ToolCallJob, "submit_result"),
)

launcher.launch(ToolWorker)
launcher.launch(one=ClaimWorker, two=ClaimWorker)
```

身份默认用类上的 `worker_name`；同一类插两块时用关键字参数命名。
`launch` 一次做完：收集 Slot、实例化 Worker、分配切面，再调用
每个 `worker.attach`（插上即运行）。`shutdown` 调用每个 `worker.detach`。

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

# 29. Launcher

`Launcher` 是控制面板，不是硬件。它读取每个 Worker 声明的 Slot，创建
`BusForWorker` 后调用 `attach`；重复 Slot 直接形成多 Worker membership，
无需规划或安装额外拓扑。

```python
with Launcher() as launcher:
    launcher.run()       # 读 constant、开 BUS、分配 Slot、worker.attach
    launcher.shutdown()  # worker.detach
```

BUS 本身不搜索 Plugin，也不决定哪些 Worker 应该存在。Worker 生命周期就是
attach / detach，不属于 BUS。

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
 FileEngine
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

# 38. File Engine

`FileEngine` 当前不是 `EngineFactory` 的实现。

它接收一个 workspace 路径，管理这棵目录树，并创建 Firmware
file Book 对应的文件夹：

```text
<workspace>/prompts   → PromptsBook
<workspace>/skills    → SkillsBook
```

BaseFileBook 表示：

> **由一组具名文件构成的 Book。**

基础能力包括：

```text
read
write
exists
delete
iterate
```

路径必须落在 Book 目录内，写入是原子的。
`PromptsBook` 用无后缀相对路径（例如 `agent/soul`）对应 `.md` 文件。
`SkillsBook` 把每个含 `SKILL.md` 的子目录当成一个 skill，并在空目录时
从包装内的默认 skills 拷入。

当前 FileEngine 不是完整 JobBoard persistence 的替代品。

---

# 39. 当前 Backend 定位

因此当前架构应准确理解为：

```text
SQL BUS persistence:
    SQLite
    PostgreSQL

File Book persistence:
    FileEngine
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
Bus(workspace)
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
claim_post_publish + all submit_post_publish
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
│   │   ├── engine.py
│   │   ├── file.py
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
    ├── launcher.py
    ├── worker.py
    └── demo/
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

12. **每个 Slot 可由多个 live Worker attach；Worker 只能调用自己 attach 的 Slot。**

13. **普通 claim 与 submit_result 均为 first-wins；同一 Job 只会被一个 Worker claim。**

14. **BUS 不根据 Plugin priority、加载顺序等策略自动筛选 Slot member。**

15. **Post Gate 的 submit 操作由 JobBoard 收集全部 live submitter 的结果。**

16. **Heartbeat lease 是 Slot liveness/membership 机制，不代表 BUS 必须多进程运行。**

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

Shared Slot membership
    allows multiple Workers to invoke one operation

Expired Slot member
    is removed without affecting other members

Post-Publish Hook
    can approve or reject Job

Post-Result Hook
    can approve or reject Result

Post Gate submission
    waits for all live submitter results and merges failures

Launcher
    attaches Workers directly to their declared Slots
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

## 49.3 Slot 使用统一多成员模型

所有 Slot 都使用同一种多 Worker membership；普通操作的业务语义由 JobStatus
转换保证，post gate 的 all-members 提交由 JobBoard 保证。

## 49.4 Hook 使用显式 Gate，而不是抽象 Pre/Post 列表

当前协议采用：

```text
claim_post_publish / submit_post_publish
claim_post_result  / submit_post_result
```

通过显式状态转换形成可观察、可恢复的 Gate。

## 49.5 Heartbeat 服务于 Runtime membership

Heartbeat 不是为了提前设计分布式系统，而是移除失活 Worker 的 Slot membership，
并避免 Gate 永久卡住。

---

# 50. 设计总结

当前 MAGI-BUS 的核心可以概括为：

> **Book 保存当前状态。**

> **Job 表达系统中需要发生、正在发生或已经发生的语义行为。**

> **JobBoard 持久化 Job 并管理其生命周期。**

> **OperateBookJobBoard 将外部 Book 操作转化为 BUS 内部事务。**

> **Job History 本身承担操作历史。**

> **Slot 定义 Worker 可以接入 JobBoard 生命周期的位置。**

> **所有 Slot 使用统一多 Worker membership；JobBoard 定义每种操作的并发语义。**

> **BusForWorker 为 Worker 提供受 Slot membership 约束的 BUS access slice。**

> **Firmware 定义当前 MAGI 实际拥有的 Book、Job、Result 与数据库版本。**

> **Launcher 只装配 Worker；它不再决定 Slot 的共享拓扑。**

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
