# Channels Worker 设计书

> 版本: v3.0 | 日期: 2026-08-08 | 状态: Approved with full-cutover contract
>
> v3.0 采用**全新 NewBus schema**：不兼容、不迁移、不双写旧 `magi.bus`
> 的表、字段、Worker 或 HTTP 运行时协议。旧运行时在新链路完整实施后直接删除。
> 本文 §24 是 v3.0 的最高优先级合同；与前文 v1/v2 描述冲突时，以 §24 为准。
>
> **执行跟踪**：每个 Part 开始前请在本文件顶部加一行 `<!-- Agent X claiming Part Y -->`；完成时改成 `<!-- Agent X done Part Y at HH:MM -->`。

---

## 1. 协作开发规则

多个 Agent 同时实现本计划。这些规则防止一个 Agent 破坏另一个 Agent 的进行中工作。

1. **NO git 操作。** 不允许 `git add` / `commit` / `rebase` / `push` / `checkout` / `stash` / `reset` / `branch`——任何会移动 HEAD 或工作树状态的操作。原因：你 stage/commit 时如果另一个 Agent 有未提交编辑，会把对方半成品代码拉进 commit（或冲突解决时丢掉），静默摧毁对方工作。所有 git 操作等实现 pass 完全结束后由人工执行。
2. **把本设计书当合同。** 开始一个 Part 前，在文件顶部加一条短注释声明"Agent X starting Part Y at <files>"，让其他 Agent 看到你认领了。如果设计书显示另一个 Agent 在做同一 Part，**先停手读对方的进行中编辑**再动。
3. **写之前先看对方的实现。** 如果另一个 Agent 已经在不同文件里开始做 Part C，先读他的代码。如果他的设计*比本设计书更好*（更干净的抽象、更少的边界情况、更好的错误处理），**采纳他的**，不要为了字面匹配计划而重做。
4. **只在对方实现真的坏掉时才介入。** 坏的迹象：缺边界情况（如 `run_at` 任务无限重触发）、竞态、硬编码路径、未测试代码路径。**不要介入的迹象**：风格不同但正确性相同、命名不同、加了你没想到的辅助函数。
5. **介入时写注释说明原因。** 不要静默重写别人的代码。格式：
   ```python
   # [Agent X → Agent Y's code]: 原 _should_fire_cron 每个 15s
   # 都触发错过的 tick；设计书 §16.5 要求"每个错过的窗口最多
   # 触发一次"。此处修复。如果你的意图是另一种语义，请在
   # 设计书里 flag。
   ```
   注释里点出对方（或者他文件的最后编辑者），说明问题点。原作者能看到改动原因，如果不同意可以反推。
6. **注释是 Agent 间总线。** 没有 code reviewer——只有 Agent 互相读代码。每个非显然决策（为什么这个 shape、为什么这个 helper、为什么这个异常类型）都需要注释。两个方案都能走通时，写一行注释列出两者并说明选了哪个。
7. **测试在实现期间只读。** 不要中途跑 `pytest` 然后通过删断言或 skip 来"修"失败测试。测试失败是因为实现没完成，这是预期的。一次性测试 pass 在所有 Part 落地后跑（§20）。
8. **实现中发现计划层错误，** 改 + 在文件里留 `# [plan amendment]: <改了啥，为啥>` + 在本设计书 §23 加一行。不要静默偏离计划。

---

## 2. 背景与动机

### 2.1 现状问题（保留 v1.0）

**生命周期异构。** TG 守护线程（独立 asyncio event loop），Task APScheduler 线程（又一独立 event loop），A2A/WebUI 入站 FastAPI 路由，出站统一 `DeliveryWorker`。四种通道，四种启动/关闭方式，没有统一抽象。

**旧总线依赖。** 通道代码通过 `get_bus()` 全局单例访问 `magi.bus.Bus`（旧总线）。ProvidersWorker / ToolsWorker / AgentWorker / ProactiveWorker 已迁到 `new_bus` 构造注入。两个总线并存，通道模块孤悬。

**入站/出站职责分裂。** 入站在各通道的 `bot.py` / `scheduler.py` / `router.py` / `api/chat.py`，出站统一在 `DeliveryWorker` 的 `if claim.channel == "tg": ...elif "a2a": ...elif "webui":` 分支里。一个通道的完整行为被拆在两个文件。

### 2.2 v1.0 设计书的额外问题（v2.0 修复）

- **基类抽象止步于 start/stop。** 出站 claim loop 在 WebUIWorker / A2AWorker / TelegramWorker 三处复制粘贴——只是把 if-elif 从 `DeliveryWorker` 翻到 `ChannelWorker` 之外。
- **TaskWorker `_last_fire` 内存态。** 重启即丢；`run_at` 一次性任务没有 mark_consume，每次 poll 都触发。
- **没有 `runTaskJob` board。** `schedule_task` tool 等场景需要 fire-by-id 任务时，直接调 `TaskChannel.dispatch` 绕过 new_bus 模式。
- **apscheduler 是隐式依赖。** 仅在 cron 验证/计算用，被 TaskScheduler 拖进来。
- **旧模块（`delivery.py` / `scheduler.py` / `runner.py`）和新 Worker 不能并存。** 旧走 `get_bus()`、新走 `new_bus`，claim 同一 delivery 来源但接口不兼容。Phase 1/2/3 的过渡窗口在 new_bus-only 部署里不适用——必须一次性切。

### 2.3 目标

1. **统一 Worker 模式。** 每个通道都有自己的 worker 类，构造注入 `NewBus`，`start/stop` 生命周期对齐 `ProvidersWorker`/`ToolsWorker`。
2. **入站/出站归位。** 每个 Channel Worker 同时管入站 + 出站；旧的 `DeliveryWorker` 直接删除（无过渡）。
3. **基类承担出站 claim loop 模板。** 三个 Worker 不再复制 claim/deliver/submit 协议。
4. **Task 状态持久化。** `last_run_at` 落库；`run_at` 任务触发后 `enabled=0`。
5. **可观测性 / 重试 / 背压有明确语义。** 不留空白。

---

## 3. 现状分析（保留 + 更新）

### 3.1 现有通道一览

| 通道 | 入站机制 | 出站机制 | 生命周期 | 总线 |
|------|---------|---------|---------|------|
| **Telegram** | 守护线程 `asyncio.run(_run_forever())` + python-telegram-bot polling | 旧 `DeliveryWorker.channel=="tg"` 分支 → 原始 HTTP | `start_bot()`/`stop_bot()` 模块级单例 | `get_bus()` |
| **Task** | APScheduler BackgroundScheduler + 自有线程 + 自有 event loop | 任务触发由 Agent 的工具调用 `send_message` 驱动 | `start_scheduler()`/`stop_scheduler()` | `get_bus()` |
| **A2A** | FastAPI `/a2a/inbox` 路由 + HMAC 验证 | 旧 `DeliveryWorker.channel=="a2a"` 分支 → HTTP POST | FastAPI lifespan | `get_bus()` |
| **WebUI** | FastAPI `/chat/send` 路由 | 旧 `DeliveryWorker.channel=="webui"` 分支 → 追加 Session | uvicorn | `get_bus()` |

### 3.2 现有 Worker 模式（基准）

```python
# magi/providers/worker.py — 标准形态
class ProvidersWorker:
    bus: NewBus                     # 构造注入
    _task: asyncio.Task | None
    _stopping: bool

    async def start(self): ...      # 幂等
    async def stop(self): ...       # 幂等

# 模块级单例 + start_xxx_worker(bus=...) 工厂
_worker: ProvidersWorker | None = None
async def start_provider_worker(bus: NewBus | None = None, ...) -> ProvidersWorker: ...
```

本设计沿用此模式。

### 3.3 `ChatJob` / `DeliveryJob` 实际字段（v1.0 文档错处）

`magi/new_bus/guild/chatJob.py:28-39` 实际定义：

```python
@dataclass(frozen=True, slots=True)
class ChatJob:
    event_id: str = ""
    run_id: str = ""
    conversation_id: str | None = None
    correlation_id: str | None = None
    kind: str = "chat"
    payload: dict[str, Any] | None = None   # ← text/channel/uid/session_id 都在这里
```

`magi/new_bus/guild/deliveryJob.py:19-25`：

```python
@dataclass(frozen=True, slots=True)
class DeliveryJob:
    channel: str                     # ← 用来 claim 时 filter
    payload: dict
    destination: str | None = None
    run_id: str = ""
    job_id: str = ""
```

v1.0 设计书把 `ChatJob` 字段当成顶层（`text`/`channel`/`uid` 等），错。**所有业务字段都在 `payload` dict 里**，与 `agent-worker-new-bus.md` §2.1 一致。

---

## 4. 架构决策（v2.0 更新）

### 决策 1：Channel → Agent 的通信方向（保留）

```
Channel → Agent:     ChatJob           (bus.agent_job_board)
Agent  → Channel:    DeliveryJob       (bus.delivery_job_board)
```

理由（保留 v1.0）：一对多天然支持、生命周期解耦、职责清晰、Board 消费模式一致。

### 决策 2：每个 Channel 一个 Worker（保留）

不采用统一 `ChannelsWorker`（轮询机制不同、故障隔离、并发模型冲突、符合开闭原则）。

### 决策 3：入站出站合一（保留 + 修正）

**例外：** WebUI/A2A 的入站是 FastAPI HTTP 路由——保留为 HTTP handler，不变成 worker 的轮询。因此 `WebUIWorker` 和 `A2AWorker` 是"只管出"。`TelegramWorker` 因为有长轮询，入站也在 worker 里。

### 决策 4（新增）：出站 claim loop 提到基类

`ChannelWorker` 提供 `_claim_delivery_loop(deliver_fn, channel_label)` 模板方法，子类只写 `_deliver_X(job: DeliveryJob)`。理由：v1.0 的 if-elif-routing 反模式换成了 claim-deliver-submit 的复制粘贴；基类提取消除后者。

### 决策 5（新增）：Task 状态持久化

- `last_run_at` 已存在于 `_TaskRow` schema（`tasksBook.py:246`），只需写方法。
- `run_at` 一次性任务触发成功后调 `mark_run_at_consumed` → `enabled=0`。
- 内存中 `_next_fire: dict[task_id, datetime]` 从 `last_run_at` rehydrate，不再裸 in-memory dict。

### 决策 6（新增）：新增 `runTaskJob` board

允许 inter-worker / tool 触发：任何调用方 `bus.run_task_job_board.publish(RunTaskJob(task_id=...))`，`TaskWorker` claim 后调同一 `_fire_task`。`schedule_task` tool、WebUI 手动触发、CLI 触发都走这条路径。

### 决策 7（新增）：删 apscheduler 依赖

`tasksBook.py` 的 `validate_cron` / `next_fire` / `humanize_cron` 全部用 `croniter` 重写。`TaskScheduler` 删除后 apscheduler 在 `magi/` 下没有调用方。

### 决策 8（新增）：旧模块同步删除

`magi/channels/delivery.py`、`magi/channels/tasks/scheduler.py`、`magi/channels/tasks/runner.py` 在本批次全部删除。**不存在"过渡期双消费"**——新 Worker 走 `new_bus`，旧 Worker 走旧 `magi.bus.Bus`，二者 claim 不同的 outbox 表（旧 `bus.delivery`，新 `bus.delivery_job_board`），本就可以并存但行为分裂；用户选择一次切干净。

---

## 5. 整体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                        NewBus                                     │
│                                                                   │
│  agent_job_board          delivery_job_board    run_task_job_board│
│  (ChatJob publish/claim)  (DeliveryJob)         (RunTaskJob)      │
│       ▲                         │                   ▲             │
│       │ publish                 │ claim             │ claim        │
│       │                         ▼                   │             │
│  ┌────┴──────────────────────────┐    ┌─────────────┴─────┐       │
│  │ Channel Worker (入站)          │    │ TaskWorker         │       │
│  │   TelegramWorker  ────────────┼──▶ │  ├─ cron 轮询     │       │
│  │   WebUI / A2A FastAPI 路由     │    │  ├─ run_at 触发   │       │
│  └───────────────────────────────┘    │  └─ runTaskJob    │       │
│                                       └───────────────────┘       │
│  agent_job_board ←── AgentWorker  处理 ChatJob → publish DeliveryJob
│       │                                                            │
│       │ claim                                                      │
│       ▼                                                            │
│  AgentWorker ──────▶ delivery_job_board                            │
│       │                                                            │
│       │ claim by channel                                           │
│       ▼                                                            │
│  Channel Worker (出站)  TelegramWorker / WebUIWorker / A2AWorker   │
└──────────────────────────────────────────────────────────────────┘
```

**Worker 全景：**

| Worker | 入站 | 出站 | 触发方式 |
|--------|------|------|---------|
| `TelegramWorker` | python-telegram-bot 长轮询 | claim delivery(channel=tg) → HTTP | 自启 |
| `TaskWorker` | 无（cron 轮询 + runTaskJob 认领） | 不投递 | 自启 |
| `WebUIWorker` | 无（FastAPI `/chat/send`） | claim delivery(channel=webui) → Session 追加 | 自启 |
| `A2AWorker` | 无（FastAPI `/a2a/inbox`） | claim delivery(channel=a2a) → HTTP POST | 自启 |
| `AgentWorker` | claim ChatJob | publish DeliveryJob | 自启 |

---

## 6. ChannelWorker 基类

`magi/channels/workers/base.py`：

```python
class ChannelWorker(ABC):
    bus: NewBus
    poll_seconds: float
    channel_name: str                         # abstract

    def __init__(self, bus, *, poll_seconds=0.25):
        self.bus = bus
        self.poll_seconds = poll_seconds
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._last_poll_at: datetime | None = None
        self._last_success_at: datetime | None = None
        self._last_error: str | None = None

    async def start(self) -> None: ...        # 幂等
    async def stop(self) -> None: ...         # 幂等

    # 出站模板方法（Part B 提取）
    async def _claim_delivery_loop(
        self, deliver_fn: Callable[[DeliveryJob], Awaitable[None]], channel_label: str,
    ) -> None: ...

    # 可观测性（Part E.3）
    def health(self) -> dict: ...

    @abstractmethod
    async def _run(self) -> None: ...         # 子类实现入口
```

**模块 docstring** 包含：

> Delivery retry 由 `BaseJobBoard._claim`（`magi/new_bus/guild/base.py:121`）负责：abandoned 的 DeliveryJob 在 lease 过期后被重 claim，最多 `MAX_ATTEMPTS=3` 后 `_make_exhausted_result` 标记 failed。Channel Worker 不自己重试。

> **Phase 1 Verification**（Part A-F 全部落地后跑）：
> - [ ] `grep -rE "from magi.bus import get_bus" magi/channels/` → 0 hits
> - [ ] `_runtime_lifespan` 起/停 4 个 Worker
> - [ ] TG inbound ChatJob 到达 AgentWorker
> - [ ] TG outbound DeliveryJob 到达 TG API
> - [ ] TaskWorker cron 任务每个 tick 最多触发一次
> - [ ] TaskWorker `run_at` 任务恰好触发一次
> - [ ] `RunTaskJob` 从 `schedule_task` tool 流转到 TaskRun
> - [ ] `/health/channels` 返回 4 通道 JSON
> - [ ] pending depth > 1000 触发节流警告
> - [ ] `pytest` 全部 pass；TODO 注释全部移除

---

## 7. TelegramWorker

`magi/channels/workers/telegram.py`。**两路并发**：

```python
async def _run(self) -> None:
    await asyncio.gather(self._run_inbound(), self._run_outbound())

async def _run_outbound(self) -> None:
    await self._claim_delivery_loop(self._deliver_tg, "tg")

async def _run_inbound(self) -> None:
    # python-telegram-bot Application，长轮询
    # 收到消息 → contacts_book.find_by_telegram_id → 权限检查 →
    # sessions_book.create → messages_book.append →
    # bus.agent_job_board.publish(ChatJob(payload={...}))
    # 关键：event loop 必须和 start() 同一 loop，
    # __init__ 时缓存 loop，assert get_running_loop() is self._loop
```

`_deliver_tg(job)`：复用 `magi/channels/telegram/bot.py` 里的 `send_text_raw`（保留为纯 HTTP helper）。

---

## 8. TaskWorker

`magi/channels/workers/task.py`。

**双输入**：cron-tick poll + `run_task_job_board` claim。

```python
async def _run(self) -> None:
    self._rehydrate()                  # 从 tasks.last_run_at 重建 self._next_fire
    self._reap_stale_runs()            # Part E.4 crash recovery
    while not self._stopping:
        # 1. runTaskJob 优先
        rj = await asyncio.to_thread(self.bus.run_task_job_board.claim)
        if rj is not None:
            await self._handle_run_task_job(rj); continue

        # 2. cron / run_at 轮询
        tasks = self.bus.tasks_book.list_all_enabled_for_workers()
        now = datetime.now(timezone.utc)
        for task in tasks:
            if self._should_fire(task, now):
                await self._fire_task(task, fired_by="cron_tick", ...)
                if task.run_at and not task.cron:
                    self.bus.tasks_book.mark_run_at_consumed(task_id=task.id)

        await asyncio.sleep(self.poll_seconds)
```

**`_fire_task`** 单一实现，被 cron 和 runTaskJob 共用：
1. `tasks_book.record_run_start(task_id, trigger=fired_by)` → 返回 TaskRun
2. `_build_contextual_prompt(task)`
3. `messages_book.append(uid, session_id, role="user", text=...)`
4. `agent_job_board.publish(ChatJob(event_id=..., run_id=..., conversation_id=..., kind="task.triggered", payload={...}))`

**`_should_fire_cron`**：用 croniter 算 `get_prev(datetime)`，对比 `self._next_fire[task.id]`。**每个错过的窗口最多触发一次**（与旧 APScheduler coalesce 等价）。

**`__init__`**：`from croniter import croniter as _croniter` 在模块顶部；不再 hot-path import。

**`schedule_desc`**：if-elif-else 显式分支。

---

## 9. WebUIWorker

`magi/channels/workers/webui.py`。出站 only：

```python
async def _run(self) -> None:
    await self._claim_delivery_loop(self._deliver_webui, "webui")

async def _deliver_webui(self, job: DeliveryJob) -> None:
    session_id = str(job.payload.get("session_id") or "")
    uid = job.payload.get("uid")
    if not session_id or not isinstance(uid, int):
        raise ValueError("webui delivery missing session_id or uid")
    self.bus.messages_book.append(
        uid=uid, session_id=session_id, role="assistant",
        text=str(job.payload.get("text") or ""),
    )
```

入站由 FastAPI `/chat/send`（`magi/channels/api/chat.py`）处理——把 `get_bus()` 替换成通过 FastAPI dependency 注入的 `bus`（或 `_current_bus` module-level helper）。

---

## 10. A2AWorker

`magi/channels/workers/a2a.py`。出站 only：

```python
async def _run(self) -> None:
    await self._claim_delivery_loop(self._deliver_a2a, "a2a")

async def _deliver_a2a(self, job: DeliveryJob) -> None:
    from magi.channels.a2a.transport import send_a2a_delivery
    await send_a2a_delivery(int(job.destination), job.job_id, job.payload)
```

入站 `magi/channels/a2a/router.py`（HMAC + `/a2a/inbox`）：把 `get_bus()` 替换为 `bus` 注入；`bus.agent_runs.publish_input` → `bus.agent_job_board.publish(ChatJob(...))`。

---

## 11. 旧模块废弃（同步删除）

`magi/channels/delivery.py` 整个文件删除。
`magi/channels/tasks/scheduler.py` 整个文件删除。
`magi/channels/tasks/runner.py` 整个文件删除。
`magi/channels/tasks/channel.py`：`TaskChannel.dispatch` 保留为 deprecated wrapper，内部转发到 `bus.run_task_job_board.publish(...)`。
apscheduler 依赖在 `magi/` 下没有其他调用方，可从 `pyproject.toml` / `requirements*.txt` 删除（实施时确认）。

---

## 12. 启动与组合根

### 12.1 模块级单例（项目惯例）

`magi/channels/workers/__init__.py`：

```python
_telegram: TelegramWorker | None = None
_task: TaskWorker | None = None
_webui: WebUIWorker | None = None
_a2a: A2AWorker | None = None
_registry: dict[str, ChannelWorker] = {}     # ← 健康端点查这里

async def start_channel_workers(bus: NewBus, *, enabled: set[str]) -> dict[str, ChannelWorker]:
    unknown = enabled - _KNOWN_CHANNELS
    if unknown:
        logger.warning("channels: ignoring unknown enabled names: %s", sorted(unknown))
    ...

def registered_channel_workers() -> dict[str, ChannelWorker]:
    return dict(_registry)
```

### 12.2 `_runtime_lifespan` 集成

`magi/startup/runtime.py`：

```python
# 启动顺序：provider → tool → mcp → agent → proactive → channels
channel_workers = await start_channel_workers(new_bus, enabled=set(channels))

# 反向停止
```

旧的 `start_channel(name)` / `stop_channel(name)` / `is_channel_running(name)` TG wrappers 删除。`POST /api/channels` 改用 `workers._registry[name].start(bus)` / `.stop()`。

---

## 13. 数据流总览

### 13.1 Telegram 全链路

```
TG User 发消息
   │
   ▼
TelegramWorker._on_tg_message
   │
   ├─ bus.contacts_book.find_by_telegram_id(tgid)
   ├─ bus.sessions_book.create(...)          # 一个 TG chat 一个 session
   ├─ bus.messages_book.append(...)
   └─ bus.agent_job_board.publish(ChatJob(
          event_id="telegram:...",
          run_id="...",
          conversation_id="...",
          kind="chat",
          payload={"text": ..., "channel": "tg", "uid": ...,
                   "session_id": ..., "tg_chat_id": ..., "tg_message_id": ...},
      ))
   │
   ▼
AgentWorker._process (claim ChatJob) → LLM → publish DeliveryJob(
       channel="tg",
       destination=tg_chat_id,
       payload={"text": "reply..."},
   )
   │
   ▼
TelegramWorker._run_outbound
   │
   ├─ claim DeliveryJob(channel="tg")
   ├─ HTTP POST sendMessage → TG API
   └─ submit_result(success=True)
```

### 13.2 简化图

```
External Input                MAGI Internal                    External Output
═══════════════               ═════════════                    ════════════════

TG User ──▶ TelegramWorker ──▶ ChatJob ──▶ AgentWorker
Task cron ──▶ TaskWorker ──▶ ChatJob ──▶    │
RunTaskJob ──▶ TaskWorker ──▶ ChatJob ──▶   │
A2A HTTP ─▶ FastAPI Route ──▶ ChatJob ──▶   │
WebUI HTTP ▶ FastAPI Route ──▶ ChatJob ──▶   ▼
                                    delivery_job_board
                                         │
                  ┌──────────────────────┼──────────────────────┐
                  ▼                      ▼                      ▼
          TelegramWorker            WebUIWorker            A2AWorker
                  │                      │                      │
                  ▼                      ▼                      ▼
              TG API               Session 追加          Peer MAGI
```

---

## 14. Part A 代码清理

### A.1 TelegramWorker `DeliveryResult` import

`magi/channels/workers/telegram.py`：

```python
if TYPE_CHECKING:
    from magi.new_bus import NewBus
    from magi.new_bus.guild.chatJob import ChatJob
    from magi.new_bus.guild.deliveryJob import DeliveryJob, DeliveryResult   # ← 补 DeliveryResult
```

### A.2 `croniter` 提到模块顶部

`magi/channels/workers/task.py`：

```python
from croniter import croniter as _croniter
```

### A.3 `schedule_desc` if-elif

替换嵌套三元链：

```python
if task.cron:
    schedule_desc = task.cron
elif task.run_at:
    schedule_desc = f"once at {task.run_at}"
else:
    schedule_desc = "ad-hoc"
```

### A.4 `enabled` 集合验证

```python
_KNOWN_CHANNELS = frozenset({"tg", "webui", "a2a", "scheduled"})
```

未知名记 warning，不静默跳过。

### A.5 `channels/api/channels.py` 迁移

`magi/channels/api/channels.py`：

- `get_bus().settings.get/set` → `bus.settings_book.get/set`（bus 通过 FastAPI dependency 注入，或 module-level `_current_bus` helper）。
- `start_channel(name)` / `stop_channel(name)` / `is_channel_running(name)` 从 `magi.startup.runtime` 删；改用 `workers._registry[name].start()` / `.stop()`。

### A.6 `channels/api/app.py` 移除 inline `start_bot`

`magi/channels/api/app.py` lines 109-123 的 `if start_telegram: start_bot()` 块删除。`create_app` 的 `start_telegram` 参数删除（默认 true 的语义失效，因为现在 TG 由 `_runtime_lifespan` 起）。

---

## 15. Part B 基类抽象

### B.1 `_claim_delivery_loop` 模板方法

`magi/channels/workers/base.py`：

```python
async def _claim_delivery_loop(
    self,
    deliver_fn: Callable[[DeliveryJob], Awaitable[None]],
    channel_label: str,
) -> None:
    """Template: backpressure check → claim → deliver_fn → submit_result.

    ``deliver_fn`` 是 async (DeliveryJob) -> None，失败时 raise。
    本方法处理 backpressure throttle（Part E.2）+ claim 异常 +
    submit_result 一次（成功或失败）。
    """
    max_depth = self._read_max_queue_depth()
    while not self._stopping:
        # ── backpressure ──────────────────────────────────────
        depth = self._bus_depth(channel_label)
        if depth > max_depth:
            self._log_backpressure_throttle(channel_label, depth)
            await asyncio.sleep(self.poll_seconds * 5)
            continue

        # ── claim ─────────────────────────────────────────────
        try:
            job = await asyncio.to_thread(
                self.bus.delivery_job_board.claim, channel=channel_label,
            )
        except Exception:
            logger.exception("channels[%s]: claim failed", channel_label)
            await asyncio.sleep(self.poll_seconds)
            continue
        if job is None:
            await asyncio.sleep(self.poll_seconds)
            continue

        self._last_poll_at = datetime.now(timezone.utc)

        # ── deliver + submit_result ───────────────────────────
        try:
            await deliver_fn(job)
            self._last_success_at = datetime.now(timezone.utc)
            self.bus.delivery_job_board.submit_result(
                key=job.job_id,
                result=DeliveryResult(job_id=job.job_id, success=True),
            )
        except Exception as exc:
            self._last_error = str(exc)
            logger.exception("channels[%s]: delivery %s failed", channel_label, job.job_id)
            self.bus.delivery_job_board.submit_result(
                key=job.job_id,
                result=DeliveryResult(
                    job_id=job.job_id, success=False, error=str(exc)[:1024],
                ),
            )
```

`_read_max_queue_depth()` 读 `bus.settings_book.get("channels.delivery.max_queue_depth")`，默认 1000。

`_bus_depth(channel)` 调新加的 `BaseJobBoard.pending_count(channel=...)`（Part E.2）。

`_log_backpressure_throttle` 每个 channel 每分钟最多打一次 warning（避免日志洪水）。

### B.2 子类瘦下来

`WebUIWorker._run` ≈ 1 行（调模板）；`A2AWorker._run` ≈ 1 行；`TelegramWorker._run_outbound` ≈ 1 行。每个子类只写 `_deliver_X(job: DeliveryJob) -> None`。

---

## 16. Part C TaskWorker 状态持久化

### C.1 `TaskBook` 新增 4 个方法

`magi/new_bus/library/local/tasksBook.py`：

```python
def record_run_start(
    self, *, task_id: str, trigger: str, run_id: str | None = None,
) -> TaskRun:
    """Insert a task_runs row, write task.last_run_at.

    trigger ∈ closed set:
      'cron_tick' | 'run_at_consume' | 'manual_run' |
      'api_manual_run' | 'schedule_task_tool'
    """
    run_id = run_id or uuid.uuid4().hex
    started_at = utcnow_naive().isoformat()
    with self._session() as s:
        run_row = _TaskRunRow(
            id=run_id, task_id=task_id, trigger=trigger,
            started_at=started_at, status="running",
        )
        s.add(run_row)
        task = s.scalar(select(_TaskRow).where(_TaskRow.id == task_id))
        if task is not None:
            task.last_run_at = started_at
        s.commit()
        return self._row_to_dto(run_row)


def record_run_end(
    self, *, task_id: str, status: str, error: str | None = None,
) -> None:
    """status ∈ {'completed', 'failed'}.

    Resets consecutive_failures on success; increments on failure (capped at 9999).
    """
    with self._session() as s:
        task = s.scalar(select(_TaskRow).where(_TaskRow.id == task_id))
        if task is None: return
        task.last_status = status
        task.last_error = (error or "")[:500] or None
        if status == "completed":
            task.consecutive_failures = 0
        else:
            task.consecutive_failures = min((task.consecutive_failures or 0) + 1, 9999)
        s.commit()


def mark_run_at_consumed(self, *, task_id: str) -> None:
    """One-shot run_at: set enabled=0 after successful fire."""
    with self._session() as s:
        task = s.scalar(select(_TaskRow).where(_TaskRow.id == task_id))
        if task is None: return
        task.enabled = 0
        s.commit()


def list_all_enabled_for_workers(self) -> list[Task]:
    """Per-user scan across all uids — workers only path.

    The uid-scoped list_enabled(uid) is preserved for user-facing UI;
    this is a separate primitive for the cron poll loop that needs
    to scan every user's tasks.
    """
    with self._session() as s:
        rows = s.scalars(
            select(_TaskRow).where(
                _TaskRow.enabled == 1,
                _TaskRow.source == SOURCE_USER,
            )
        ).all()
        return [self._row_to_dto(r) for r in rows]
```

### C.2 `croniter` 替换 apscheduler

`tasksBook.py` 当前的 `from apscheduler.triggers.cron import CronTrigger` 删掉。`validate_cron` / `next_fire` / `humanize_cron` 全部用 `croniter` 重写。`preset_to_cron` 是从结构化 form 转 cron string 的纯函数，与解析器无关，保留。`validate_run_at` / `validate_run_at_future` 用 `datetime.fromisoformat`，与 apscheduler 无关，保留。

### C.3 TaskWorker rehydrate

```python
def _rehydrate(self) -> None:
    """Rebuild self._next_fire from each task's last_run_at column."""
    tasks = self.bus.tasks_book.list_all_enabled_for_workers()
    self._next_fire = {
        t.id: datetime.fromisoformat(t.last_run_at) if t.last_run_at else None
        for t in tasks
    }
    logger.info("TaskWorker: rehydrated %d enabled task(s)", len(tasks))
```

### C.4 `_should_fire_cron`（coalesce 等价）

```python
def _should_fire_cron(self, task: Task, now: datetime) -> bool:
    """Fire at most once per missed window (coalesce-equivalent)."""
    if not task.cron:
        return False
    cron_iter = _croniter(task.cron, now)
    prev_fire = cron_iter.get_prev(datetime)
    last = self._next_fire.get(task.id)
    return last is None or (prev_fire and prev_fire > last)
```

### C.5 `run_at` 消费

`_fire_task` 成功后，若 `task.run_at and not task.cron`，调 `mark_run_at_consumed(task.id)`。

---

## 17. Part D runTaskJob

### D.1 `magi/new_bus/guild/runTaskJob.py`

```python
from __future__ import annotations
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.guild.base import BaseJobBoard


@dataclass(frozen=True, slots=True)
class RunTaskJob:
    task_id: str
    manual: bool = True
    fired_by: str = "manual"          # closed set:
                                       # cron_tick | run_at_consume |
                                       # api_manual_run | schedule_task_tool
    session_id: str | None = None
    uid: int | None = None
    job_id: str = ""


@dataclass(frozen=True, slots=True)
class RunTaskResult:
    job_id: str
    success: bool
    run_id: str | None = None
    error: str | None = None


class _RunTaskJobRow(Base):
    __tablename__ = "run_task_jobs"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    manual: Mapped[int] = mapped_column(Integer, default=1)
    fired_by: Mapped[str] = mapped_column(String(32), default="manual")
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    uid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive,
    )


class runTaskJobBoard(BaseJobBoard[_RunTaskJobRow, RunTaskJob, RunTaskResult]):
    job_model = _RunTaskJobRow
    job_cls = RunTaskJob
    result_cls = RunTaskResult
    natural_key_attr = "job_id"

    def publish(self, job: RunTaskJob) -> str:
        with self._session() as s:
            row = _RunTaskJobRow(
                job_id=uuid.uuid4().hex, status="pending",
                task_id=job.task_id, manual=int(job.manual),
                fired_by=job.fired_by,
                session_id=job.session_id, uid=job.uid,
            )
            s.add(row); s.flush(); s.commit()
            return row.job_id
```

### D.2 接入 `NewBus`

`magi/new_bus/bootstrap.py`：
- `NewBus` dataclass 加 `run_task_job_board: object  # runTaskJobBoard`
- `_bootstrap_with_dirs`：lazy import `RunTaskJobBoard`，实例化，传给 `NewBus(...)`

### D.3 TaskWorker 双输入

见 §8。`_handle_run_task_job(rj)` 复用 `_fire_task`：

```python
async def _handle_run_task_job(self, rj: RunTaskJob) -> None:
    try:
        task = self.bus.tasks_book.get(task_id=rj.task_id)
        if task is None:
            self.bus.run_task_job_board.submit_result(
                key=rj.job_id,
                result=RunTaskResult(rj.job_id, False, error="task not found"),
            ); return
        run = await self._fire_task(
            task, fired_by=rj.fired_by,
            session_id=rj.session_id or task.session_id,
            uid=rj.uid or task.uid,
        )
        self.bus.run_task_job_board.submit_result(
            key=rj.job_id,
            result=RunTaskResult(rj.job_id, True, run_id=run.id),
        )
    except Exception as exc:
        self.bus.run_task_job_board.submit_result(
            key=rj.job_id,
            result=RunTaskResult(rj.job_id, False, error=str(exc)[:1024]),
        )
```

### D.4 调用方切换

- `magi/tools/schedule_task.py`（LLM-side tool）：`TaskChannel.dispatch(...)` → `bus.run_task_job_board.publish(RunTaskJob(task_id, fired_by="schedule_task_tool", ...))`。先用 grep 确认工具的精确路径。
- `magi/channels/api/tasks.py` 的 `POST /api/tasks/{id}/run`（手动触发）：同路径，`fired_by="api_manual_run"`。

---

## 18. Part E 工程完备性

### E.1 重试策略（仅文档）

`ChannelWorker` docstring 写明 DeliveryJob 重试由 `BaseJobBoard._claim`（`magi/new_bus/guild/base.py:121`）的 lease 机制负责：abandoned 的 job 在 lease 过期后被重 claim，最多 `MAX_ATTEMPTS=3` 后 `_make_exhausted_result` 标记 failed。**Channel Worker 不自己重试。**

### E.2 背压

`magi/new_bus/guild/base.py` 加：

```python
from sqlalchemy import func

def pending_count(self, *, channel: str | None = None) -> int:
    """Count rows in pending state, optionally filtered by `channel`.

    Used by ChannelWorker._claim_delivery_loop for backpressure.
    """
    with self._session() as s:
        stmt = select(func.count()).select_from(self.job_model).where(
            self.job_model.status == "pending"
        )
        if channel is not None and hasattr(self.job_model, "channel"):
            stmt = stmt.where(self.job_model.channel == channel)
        return int(s.scalar(stmt) or 0)
```

阈值从 `bus.settings_book.get("channels.delivery.max_queue_depth")` 读，默认 1000。超阈值 → 每分钟每 channel 一次 warning + poll 间隔 ×5。

### E.3 可观测性

`ChannelWorker.health()` 返回：

```python
{
    "name": self.channel_name,
    "running": self._task is not None and not self._task.done(),
    "last_poll_at": self._last_poll_at.isoformat() if self._last_poll_at else None,
    "last_success_at": self._last_success_at.isoformat() if self._last_success_at else None,
    "last_error": self._last_error,
    "queue_depth": self._bus_depth(self.channel_name),
}
```

新文件 `magi/channels/api/health.py`：

```python
router = APIRouter(tags=["health"])

@router.get("/health/channels")
async def health_channels() -> dict:
    from magi.channels.workers import registered_channel_workers
    return {"channels": [
        w.health() for w in registered_channel_workers().values()
    ]}
```

挂到 `magi/channels/api/app.py` 的 `/health` 路由之后。

### E.4 崩溃恢复

- **TaskWorker**：`_rehydrate` 末尾调 `TaskRunBook.reap_stale(older_than_seconds=300)`——把 status="running" 但 started_at < now-300s 的 task_runs 行翻成 failed，error="abandoned by previous worker"。新增方法在 `TaskRunBook`。

  ```python
  def reap_stale(self, *, older_than_seconds: int = 300) -> int:
      """Flip stuck 'running' rows to 'failed'. Returns count."""
      cutoff = (datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)).isoformat()
      with self._session() as s:
          rows = s.scalars(
              select(_TaskRunRow).where(
                  _TaskRunRow.status == "running",
                  _TaskRunRow.started_at < cutoff,
              )
          ).all()
          for row in rows:
              row.status = "failed"
              row.error = "abandoned by previous worker"
              row.finished_at = utcnow_naive().isoformat()
          s.commit()
          return len(rows)
  ```

- **TelegramWorker**：`python-telegram-bot` `Application.updater.start_polling` 自动重连网络错误；硬失败时 `_run_inbound` task 退出，需要操作员重启 process。
- **WebUIWorker / A2AWorker**：无状态 claim loop，lease 机制自动处理。

### E.5 Phase 1 验收清单

见 §6 模块 docstring 末尾的 checklist。所有项必须勾选完才能算 Phase 1 完成。

---

## 19. Part F 测试一次性通过

### 19.1 测试 rebind 模式

所有现有测试里的 `magi.bus.get_bus()` 替换为 `bootstrap_new_bus(state_dir=tmp_path/"memories")`。字段映射：

| 旧 bus | new_bus |
|--------|---------|
| `bus.task` | `bus.tasks_book` |
| `bus.task_runs` | `bus.task_runs_book` |
| `bus.session` | `bus.sessions_book` |
| `bus.session.append_messages(...)` | `bus.messages_book.append(...)` |
| `bus.delivery` | `bus.delivery_job_board` |
| `bus.agent_runs.publish_input(AgentMessage)` | `bus.agent_job_board.publish(ChatJob(payload={...}, kind="..."))` |
| `bus.contacts` | `bus.contacts_book` |
| `bus.contacts.find_by_telegram_id` | `bus.contacts_book.find_by_telegram_id` |
| `bus.settings.get/set` | `bus.settings_book.get/set` |

### 19.2 受影响测试

- `tests/unit/test_delivery_worker.py` — 改用 `delivery_job_board` + `TelegramWorker._deliver_tg`，mock `send_text_raw`，断言 `submit_result(success=True)` 落库。删 `TODO migrate to new_bus` 头注。
- `tests/unit/test_task_scheduler.py` — 改测 `TaskWorker.__init__` + `_fire_task`，断言 cron 每 tick 一次、`run_at` 一次。删 TODO。
- `tests/unit/test_task_channel.py` — 改测 `RunTaskJob` publish→claim→submit_result。删 TODO。
- `tests/unit/test_tasks_once_model.py` — 保留模型测试，rebase fixture 到 `bootstrap_new_bus`（原 `init_orm`/`init_sqlite`）。删 TODO。
- `tests/unit/test_tasks_api_inference.py` — 同上。

### 19.3 新增测试

- `tests/unit/test_run_task_job.py` — publish/claim/submit_result；lease 过期回 pending；MAX_ATTEMPTS=3 触发 `_make_exhausted_result`。
- `tests/unit/test_task_run_persistence.py` — `record_run_start` 写 TaskRun + bump `last_run_at`；`record_run_end("completed")` reset failures；`record_run_end("failed")` increment + 写 error；`mark_run_at_consumed` 设 `enabled=0`；`list_all_enabled_for_workers` 不带 uid 过滤。
- `tests/unit/test_channel_worker_template.py` — fake `delivery_job_board.claim` 返 job；断言 `_claim_delivery_loop` 调 `deliver_fn` 后 `submit_result(success=True)`；backpressure 分支在 `pending_count > max_depth` 时早退（mock `settings_book.get`）。
- `tests/unit/test_health_channels.py` — 起 4 个 Worker，hit `/health/channels`，断言 4 条 entry。

---

## 20. 验证

Part A-F 全部落地后，跑一次：

1. `grep -rE "from magi.bus import get_bus" magi/channels/` → 期望 **0** hits。
2. `pytest tests/unit/test_run_task_job.py tests/unit/test_task_run_persistence.py tests/unit/test_channel_worker_template.py tests/unit/test_health_channels.py tests/unit/test_delivery_worker.py tests/unit/test_task_scheduler.py tests/unit/test_task_channel.py tests/unit/test_tasks_once_model.py tests/unit/test_tasks_api_inference.py -v` → 全部 pass。
3. 走 §6 模块 docstring 的 Phase 1 Verification checklist 顶到底。
4. 操作员手工冒烟（非 pytest）：TG inbound → ChatJob → Agent → DeliveryJob → TG outbound；cron 任务恰好触发一次；`/health/channels` 返回 JSON。

---

## 21. 风险与注意事项

1. **TelegramWorker event-loop 不匹配。** 旧 `bot.py` 在 daemon thread 自有 asyncio loop；python-telegram-bot v21 共享 loop 可行，但 `Application.initialize()` 必须和 `start()` 在同一 loop。*缓解：* `__init__` 缓存 loop，`_run_inbound` 入口 `assert asyncio.get_running_loop() is self._loop`。

2. **ChatJob payload 契约漂移。** 本设计的 payload 键集合（`text` / `channel` / `uid` / `session_id` / `task_id` / `task_run_id` / `tg_chat_id` / `tg_message_id`）与 `agent-worker-new-bus.md` §2.1 对齐。`chatJob.py` 或 `AgentWorker._process` 的未来修改可能静默破坏 TaskWorker。*缓解：* `test_run_task_job.py` 加 payload 键断言。

3. **背压默认 1000 静默压制合法突发。** *缓解：* 阈值是 `settings_book` key（`channels.delivery.max_queue_depth`），操作员可调；warning 每分钟每 channel 一次。

4. **`record_run_start` 与 `agent_job_board.publish` 之间崩溃。** task_runs 行留在 `status="running"`。*缓解：* `TaskRunBook.reap_stale(older_than_seconds=300)` 在 `_rehydrate` 末尾跑（§18 E.4）；加单元测试。

5. **`channels.enabled` toggle 漏改。** `channels/api/channels.py` 必须同步迁到 `bus.settings_book`；否则 WebUI 切换按钮不真启动/停 worker。*缓解：* Part A.5 覆盖；若推迟则在 §23 plan amendments 标。

6. **A2A outbound 依赖 `MAGI_RUNTIME_ID` 环境变量。** `channels/a2a/transport.py:17`。*缓解：* test fixture 里 set env；`A2AWorker` docstring 注明。

7. **Worker 启动与 FastAPI lifespan 的竞态。** `_lifespan` 调 `worker_lifespan()`；若 FastAPI app 在 lifespan 跑之前被 import，`registered_channel_workers()` 是空的，`/health/channels` 返 `[]`。*缓解：* 测试用 `with TestClient(app) as client`（触发 lifespan）。

---

## 22. 复用的现有工具

- `BaseJobBoard.claim` / `submit_result` / `get_result` — [`magi/new_bus/guild/base.py:67-82`](../../magi/new_bus/guild/base.py#L67-L82)。所有 Channel Worker 出站循环走这些。
- `BaseJobBoard._claim` lease 恢复 — [`magi/new_bus/guild/base.py:121-139`](../../magi/new_bus/guild/base.py#L121-L139)。自动重试 abandoned 任务最多 3 次。
- `BaseBook._row_to_dto` — [`magi/new_bus/library/base.py:59`](../../magi/new_bus/library/base.py#L59)。所有 `record_run_*` / `list_*` 返回值走这里。
- `NewBus` 字段 — [`magi/new_bus/bootstrap.py:31-151`](../../magi/new_bus/bootstrap.py#L31-L151)。`agent_job_board` 即 `chatJobBoard`；`delivery_job_board` 是出站队列。
- `ChannelEnum` — [`magi/new_bus/library/local/tasksBook.py:64-90`](../../magi/new_bus/library/local/tasksBook.py#L64-L90)。`enabled` 验证（Part A.4）从这里取。
- `bootstrap_new_bus` — [`magi/new_bus/bootstrap.py:158`](../../magi/new_bus/bootstrap.py#L158)。test fixture 的入口。
- 模块级单例 + `start_xxx_worker(bus=...)` 模式 — [`magi/providers/worker.py:547-582`](../../magi/providers/worker.py#L547-L582)。`start_channel_workers` 镜像此模式。
- `_SessionMessage` 等 webui 出站所需 dataclass（如果还需要）：见 `magi/channels/delivery.py:64-72` 的兼容层，迁移后保留。

---

## 23. Plan amendments

实现期间发现需要偏离本设计书时，在下方加一行（含 Agent 名、时间、原因、影响范围）。**不要静默改代码不记录。**

| # | 时间 | Agent | 偏离内容 | 原因 | 影响范围 |
|---|------|-------|---------|------|---------|
| — | — | — | （实施时填） | — | — |
