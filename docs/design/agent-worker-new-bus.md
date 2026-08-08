# AgentWorker 迁移到 new_bus 设计书

## 状态

**当前阶段**：骨架就位，核心设计待落地。

`magi/agent/worker.py` 已完成第一轮重构（构造注入 `NewBus`、去掉 `magi.bus` 依赖），所有旧 `magi.bus` store 调用已替换为 `raise NotImplementedError(...)` 占位 stub。`magi/startup/runtime.py` 已改为 `start_agent_worker(bus=new_bus)`。其余 agent 子模块（`agent_context.py`、`system_prompt.py`、`compaction.py`、`auto_title.py` 等）仍走老 `magi.bus`，按"以后再修"策略暂不动。

本设计书覆盖 `worker.py` 的下一步实现、必要的持久化状态机，以及后续
Agent / Channel 迁移的边界。它是实现合同：文中的 DTO 字段和事务边界必须先
落实，不能把示例中的字段当作当前代码已经提供的 API。

> **v3 storage profile（与 `channels-worker-design.md` §24 一致）**：最终运行时
> 使用全新 `agent_turn_jobs`、`agent_turns`、`agent_messages`、
> `channel_delivery_jobs` 与 `a2a_request_jobs` schema。本文早先出现的
> `agent_inbox`、`delivery_outbox`、现有 `ChatJob` 字段形状只描述迁移前代码，
> 不构成目标兼容要求；不迁移、不双写、不读取旧 Bus 表。

---

## 1. 架构总览

```
                          ┌──────────────┐
   Channel (TG/API) ────→│ agent_job_board│  (ChatJob on agent_inbox)
                          └──────┬───────┘
                                 │ claim / claim_for_conversation
                          ┌──────▼───────┐
                          │  AgentWorker  │
                          │ AgentTurnStore│
                          │               │
                          │   _process()  │
                          │     │         │
                          │     ├─ context assembly
                          │     │   (sessions_book,
                          │     │    messages_book,
                          │     │    memory_book,
                          │     │    contacts_book,
                          │     │    prompt_book,
                          │     │    skills_book,
                          │     │    tool_definitions_book)
                          │     │         │
                          │     ├─ llm_job_board.publish(CallLLMJob)
                          │     │   → wait_for_result()
                          │     │         │
                          │     ├─ [no tool] → delivery_job_board
                          │     │         │
                          │     ├─ [regular tool] → tool_job_board × N
                          │     │         │
                          │     ├─ [message_magi] → a2a_job_board × M
                          │     │         │
                          │     └─ _gather_all(tools, a2a, steering)
                          │          ├─ 并发 poll tool / a2a 结果
                          │          └─ 并发 claim_for_conversation(conv)
                          │             收集 steering 文本
                          │               │
                          └──────┬───────┘
                                 │ submit_result
                          ┌──────▼───────┐
                          │ agent_job_board│ (ChatJobResult)
                          └──────────────┘
```

**AgentWorker 只依赖 `NewBus`**。所有外部操作（LLM 调用、工具执行、消息投递、同会话 steering）通过对应的 job board 进行。AgentWorker 自身作为协调者，负责：

1. 从 `agent_job_board`（具体实现类为 `chatJobBoard`）认领 `ChatJob`
2. 驱动 agent loop（上下文组装 → LLM 推理 → 结果分发）
3. 通过 `AgentTurnStore` 原子提交 transcript、后续 jobs / outbox、turn 状态，
   最后提交 `ChatJobResult`

**命名约定**：公共 `NewBus` 字段叫 `agent_job_board`，其底层实现类叫
`chatJobBoard`，表为既有的 `agent_inbox`。本文统一使用前者；不新建
`chat_jobs` 表，也不再引入第二个 agent 输入 board。

- `ChatJob` 的稳定 envelope 是 `event_id / run_id / conversation_id / kind / payload`。
- `payload` 承载 channel 输入：`text`、`channel`、`uid`、`session_id`、
  `caller_role` 与可选 `deadline_at`。不得使用不存在的 `metadata`、`channel`
  或 `job_id` 字段。
- `ChatJobResult` 以 `event_id` 为自然键；它只表达受理结果和错误信息，回复
  正文由 committed transcript / delivery outbox 承载。

---

## 2. 核心数据结构

### 2.1 先冻结 Job 合同

以下是 Phase 1 直接使用的现有 DTO 形状；Channel 迁移必须按它发布，
Worker 也必须按它读取：

```python
ChatJob(
    event_id=source_idempotency_key,
    run_id=stable_run_id,
    conversation_id=conversation_id,
    kind="channel.message.received",  # 或 "run.cancel"
    payload={
        "text": text,
        "channel": channel,
        "uid": uid,
        "session_id": session_id,
        "caller_role": caller_role,
        "deadline_at": deadline_at_iso_or_none,
    },
)

ChatJobResult(
    event_id=job.event_id,
    success=True,
    status="completed",
    result={"run_id": job.run_id},
)
```

`RunToolJob` 的参数一律放在 `payload` 中，`SendA2AJob` 使用 `target` 和
`request`，而不是尚不存在的 `arguments`、`target_magic_id`、`text` 或
`job_id` 字段。若这些字段确有必要，应先单独变更 DTO / schema 并迁移测试，
不能由 Worker 隐式假定。

### 2.2 AgentTurn：持久化的协调状态

`RunContext` 只是进程内缓存，不能作为恢复依据。Phase 1 必须新增
`AgentTurnBook`（或同等的 `AgentTurnStore`），以 `run_id` 为主键保存：

- `conversation_id`、根 `event_id`、`uid`、`session_id`、`channel`；
- `phase`：`running_llm | waiting_effects | terminal | cancelled`；
- `iteration`、序列化的消息尾部、最近 LLM job id、待处理 tool / A2A job ids；
- `lease_owner`、`lease_until`、`cancel_requested_at`；
- terminal result、错误码及已创建的 delivery outbox id。

它提供三类事务性操作：

1. `claim_root_and_acquire_turn()`：原子认领根 ChatJob 并取得会话 lease；
2. `commit_waiting_effects()`：原子写 assistant transcript、turn continuation、
   Tool/A2A jobs；
3. `commit_terminal()`：原子写 assistant transcript、token usage、delivery outbox、
   turn terminal state 和 ChatJobResult。

LLM、工具、A2A、网络投递均在事务外执行；结果回到 Worker 后才调用上述提交。
这样崩溃恢复是从持久化 phase 继续，而不是重新执行已经产生副作用的步骤。

### 2.3 RunContext

单次 `ChatJob` 引发的完整 agent run 的全部内存状态。一次 `_process()` 调用对应一个 `RunContext` 实例。

```python
@dataclass
class RunContext:
    """Single ChatJob → agent run. All mutable state lives here."""

    # identity (from ChatJob.payload)
    run_id: str
    root_event_id: str
    uid: int | None
    session_id: str | None
    channel: str
    caller_role: str | None

    # conversation_id = ChatJob.conversation_id，用于 steering 过滤
    conversation_id: str

    # 去重 key: (uid, conversation_id) — 同一 uid + 会话不会并发跑两次
    session_key: tuple[int | None, str]

    # 累积消息历史（用于组装下一轮 CallLLMJob.messages）
    messages: list[dict]

    # agent loop 迭代上限（从 settings_book 读）
    max_iterations: int

    # 结果
    final_reply: str = ""
    final_error: str | None = None
    cancelled: bool = False

    # 尚未由下一次原子 transition 落盘的增量
    pending_steering_event_ids: list[str] = field(default_factory=list)
    pending_token_usage: dict | None = None
    delivery_address: str | None = None
```

**lifecycle**：由 `AgentWorker` 从已 lease 的 `AgentTurn` 重建；它在每次
`commit_*()` 后可以丢弃并在重启时重建。内存对象绝不能是唯一的 continuation。

**与上版对比**：不再持有 `steer_queue` / `steer_event`；steering 消息仍在
board 中，但“此 conversation 是否已有活动 turn”由 `AgentTurnBook` 这一
持久化状态协调，而非本地集合。

### 2.4 AgentWorker

```python
class AgentWorker:
    bus: NewBus                       # 构造注入
    turns: AgentTurnStore              # 与 NewBus 使用同一 local factory
    workspace: str                     # composition root 显式注入
    poll_seconds: float = 0.25

    _task: asyncio.Task | None        # 主循环 task
    _stopping: bool                   # 退出信号
    worker_id: str
```

单进程内可以缓存 `RunContext` 以减少重读，但正确性只依赖 `AgentTurnBook`。
因此可横向扩展多个 Worker，且不会让同一 conversation 并发执行。

### 2.5 agent_job_board 扩展

```python
# magi/new_bus/guild/chatJob.py

class chatJobBoard(BaseJobBoard[_AgentInboxRow, ChatJob, ChatJobResult]):
    # ... existing ...

    def claim_for_conversation(self, *, conversation_id: str) -> ChatJob | None:
        """Steering-scoped claim — 认领同会话的 pending ChatJob。

        只认领 ``AgentTurnBook`` 标记为 active 的 conversation 的后续消息。
        根消息的认领与 turn lease 获取必须走 ``AgentTurnStore`` 的同一事务，
        不能靠先 ``claim()`` 再检查内存集合。

        用法：AgentWorker 在 ``_gather_all`` 中每轮轮询调用一次，认领到
        的 ChatJob 立即 ``submit_result(success=True)`` 标记为
        consumed，不再触发独立 run，文本作为 steering 拼入下一轮 prompt。
        """
        # 实现使用 SQLite 可验证的 compare-and-set UPDATE（或 BEGIN IMMEDIATE），
        # 而不是 ``SELECT ... FOR UPDATE SKIP LOCKED``；后者不是 SQLite 的并发
        # 互斥语义。成功更新一行后才返回该 job。
        ...
```

### 2.6 lease：renew，而不是 release 自旋

Agent 的长操作可超过默认 60 秒 lease（LLM 可等待 120 秒、工具可等待 300 秒）。
因此需要 `renew_lease(key, owner)`，并在等待 LLM / tool / A2A 的期间定时续租。
续租失败表示 worker 已失去 ownership，必须停止提交结果。

不要采用“claim 到同会话 job 后 release 回 pending”的调度方式：顺序 worker 会无意义
重复认领，多个 worker 又无法共享 `_active_sessions`。根 job 由
`claim_root_and_acquire_turn()` 处理；活动 turn 的后续消息只由
`claim_for_conversation()` 消费为 steering。

```python
# magi/new_bus/guild/base.py

class BaseJobBoard(...):

    def renew_lease(self, *, key: str, owner: str) -> bool: ...
```

`cancel(key)` 也不能只是把 status 改为 cancelled：它必须是带版本/ownership
检查的状态转换，避免一个已执行完成的 worker 在稍后以 `submit_result()` 覆盖取消。

---

## 3. 主流程

### 3.1 `_run()` — 主循环

```python
async def _run(self):
    while not self._stopping:
        claimed = await asyncio.to_thread(
            self.turns.claim_root_and_acquire_turn, worker_id=self.worker_id,
        )
        if claimed is None:
            await asyncio.sleep(self.poll_seconds)
            continue
        job, turn = claimed
        if job.kind == "run.cancel":
            self.turns.request_cancel(run_id=job.run_id, event_id=job.event_id)
            continue

        ctx = self._context_from_turn(turn, job.payload or {})
        try:
            await self._process(ctx)
        except Exception as exc:
            logger.exception("agent run failed conv=%s", ctx.conversation_id)
            self.turns.commit_terminal_failure(
                run_id=ctx.run_id,
                event_id=ctx.root_event_id,
                error_code=f"agent_crashed:{type(exc).__name__}",
            )
```

**关键点**：
- 根 job 的 claim 和 conversation ownership 必须是一个持久化事务；同一 conversation
  的新消息不会被普通 claim 误当作平行 run。
- cancel 按 `run_id` 请求，落在持久化 turn 状态上，而不是按 conversation 广播。
- 异常分支必须以 `commit_terminal_failure()` 关闭 turn 与 ChatJob；不能只改内存
  `final_error`。
- `ChatJobResult` **不承载回复文本**——`ChatJobResult` 只表示"这个 job 处理完毕"，回复统一由 `_publish_delivery()` 走 `delivery_job_board` 投递。steering 场景下多个 ChatJob 共享一条 reply，`ChatJobResult` 不能 1:1 绑定 reply（见 §4.7）。
- lease 由 background heartbeat 续期；失去 lease 的 Worker 不得继续写入。

### 3.2 `_process(ctx)` — agent loop

```
┌─ _load_history(ctx) ──────────────────┐
│ 从 AgentTurn + sessions/messages 复原   │
│ 已提交的消息；绝不只依赖进程内列表        │
└────────────────────────────────────────┘
                   │
     ┌─────────────▼──────────────┐
     │  for i in range(max_iter): │
     │                            │
     │  1. _build_llm_job(ctx)    │
     │     ├─ _system_prompt()    │
     │     ├─ _tool_schemas()     │
     │     └─ compaction (后续)    │
     │                            │
     │  2. llm_job_board.publish()│
     │     → wait_for_result()    │
     │                            │
│  3. commit_waiting_effects │
│     原子提交 assistant     │
│     transcript + 后续 jobs │
     │                            │
     │  4. if no tool_uses:       │
     │       _publish_delivery()  │
     │       _maybe_title()       │
     │       return               │
     │                            │
     │  5. _split_tools()         │
     │     ├─ message_magi → A2A  │
     │     └─ regular → Tool     │
     │                            │
│  6. 等待已提交的 tool/A2A  │
     │                            │
     │  7. await _gather_all(     │  ← 内部并发：
     │       tool_results,        │    poll tool results
     │       a2a_results,         │    poll a2a results
     │       ctx)                 │    poll claim_for_conversation
     │                            │
     │  8. _append_tool_result_   │
     │     user_message(          │
     │       tool_results,        │
     │       a2a_results,         │
     │       steering_text)       │  ← steering 接在 tool_results 之后
     │                            │
     │  → loop back               │
     └────────────────────────────┘
                   │ (max_iter exceeded / cancel)
     commit_terminal()
     ctx.final_reply = "已超过最大工具调用次数..." | ""
```

---

## 4. 关键子流程

### 4.1 上下文组装 `_build_llm_job`

```python
def _build_llm_job(self, ctx: RunContext) -> CallLLMJob:
    system = self._system_prompt(ctx)         # 6-block 拼接（见 §4.2）
    messages = [{"role": "system", "content": system}] + ctx.messages
    tools = self._tool_schemas(ctx.caller_role)
    return CallLLMJob(
        messages=messages,
        max_tokens=self._read_max_tokens(),
        tools=tools,
        parameters={
            "uid": ctx.uid,
            "session_id": ctx.session_id,
            "channel": ctx.channel,
            "caller_role": ctx.caller_role,
        },
    )
```

**compaction**：完整摘要压缩可以在 Phase 2 迁移，但 Phase 1 不能无限累积消息。
在调用 LLM 前必须执行可验证的硬上限（保留最近消息并记录
`agent.context_limit_exceeded`，或先实现最小摘要）。因此 Phase 1 的验收是
“超限可预测地降级”，不是“500 条消息不 OOM”。

### 4.2 System Prompt 组装 `_system_prompt`

六块顺序拼接（与旧 `system_prompt.py` 一致）：

| # | 块 | 数据源（new_bus） |
|---|---|---|
| 1 | SOUL (persona) | `prompt_book.get_structured("soul")` 或 workspace `SOUL.md` |
| 2 | Instructions | `memberships_book.instruction_context(magic_id)` |
| 3 | Long-term memory | `memory_book.list_by_owner(uid)` |
| 4 | Current chatter | `contacts_book.get_by_uid(uid)` + `contact_notes_book.list_for_contact(uid)` |
| 5 | Daily note | `contacts_book.read_daily_note(uid)` + `settings_book.show_daily_note()` |
| 6 | Skills | `skills_book.list()` 的 meta（name + description） |

**当前策略（Phase 1 临时）**：`_system_prompt` **直接内联实现**上述逻辑，不调 `system_prompt.py`（该子模块仍走老 `magi.bus`）。Phase 2 把 `system_prompt.py` 迁到 `NewBus` 后，再切回委托调用。**注意**：内联实现等于在 worker 里复制了一份 prompt 组装逻辑，Phase 2 必须严格删掉内联版本，否则会出现两份实现漂移。

### 4.3 Tool Schemas

```python
def _tool_schemas(self, caller_role: str | None) -> list[dict]:
    defs = self.bus.tool_definitions_book.list_schemas(
        caller_role=caller_role,
        caller_admin=False,
    )
    return [
        {"name": d.name, "description": d.description, "input_schema": d.input_schema}
        for d in defs
    ]
```

### 4.4 等待全部工具结果 + steering 收集 `_gather_all`

**核心设计**：在等待 tool / a2a 结果的同时，通过 `claim_for_conversation` 主动从 board 认领同会话的 steering `ChatJob`。steering 以 board claim 方式获取，不做进程内队列传递。**A2A 与 tool 走两个独立的 task，结果分别返回**，避免 `tool_call_id` 冲突。

```python
@dataclass
class _GatherResult:
    tool_results: dict[str, RunToolResult]   # tool_call_id → result
    a2a_results: dict[str, SendA2AResult]    # tool_call_id → result
    steering_text: str | None                # 拼接的 steering 文本

MAX_STEERING_PARTS = 16         # 单次 run 最多吞多少条 steering
TOOL_WAIT_SECONDS = 300.0       # 来自 settings_book（见 §6）

async def _gather_all(
    self,
    ctx: RunContext,
    tool_entries: list[tuple[str, str]],   # [(tool_call_id, job_id), ...]
    a2a_tasks: list[asyncio.Task],          # 每个 SendA2AJob 一个 task
) -> _GatherResult:
    """并发等待 tool + a2a + steering，steering 通过 claim_for_conversation
    在每轮轮询循环中插入。
    """
    pending_tools: dict[str, str] = {jid: tid for tid, jid in tool_entries}
    tool_results: dict[str, RunToolResult] = {}
    a2a_results: dict[str, SendA2AResult] = {}
    steering_parts: list[str] = []
    deadline = asyncio.get_running_loop().time() + TOOL_WAIT_SECONDS

    while pending_tools or not all(t.done() for t in a2a_tasks):
        # ── 1. 尝试 claim 同会话的 steering ──
        if ctx.conversation_id and len(steering_parts) < MAX_STEERING_PARTS:
            steer = await asyncio.to_thread(
                self.bus.agent_job_board.claim_for_conversation,
                conversation_id=ctx.conversation_id,
            )
            if steer is not None:
                text = (steer.payload or {}).get("text") or ""
                if text:
                    steering_parts.append(text)
                # steering event_id 暂存到 ctx；与下一次 commit_waiting_effects()
                # 同一事务标记 consumed，不能在此处单独提交。
                ctx.pending_steering_event_ids.append(steer.event_id)

        # ── 2. 检查 tool 结果 ──
        for job_id, tool_id in list(pending_tools.items()):
            result = await asyncio.to_thread(
                self.bus.tool_job_board.get_result, key=job_id,
            )
            if result is not None:
                tool_results[tool_id] = result
                del pending_tools[job_id]

        # ── 3. 检查 a2a 结果（独立 task，不与 pending_tools 共享 key）──
        for task in a2a_tasks:
            if task.done() and not task.cancelled():
                ar = task.result()
                for tc_id, payload in ar.items():
                    a2a_results[tc_id] = payload

        # ── 4. 全部完成则退出 ──
        if not pending_tools and all(t.done() for t in a2a_tasks):
            break

        # ── 5. 超时兜底 ──
        if asyncio.get_running_loop().time() >= deadline:
            logger.warning(
                "agent gather timeout conv=%s pending_tools=%d",
                ctx.conversation_id, len(pending_tools),
            )
            for job_id, tool_id in pending_tools.items():
                tool_results[tool_id] = RunToolResult(
                    job_id=job_id, success=False,
                    content="tool execution timed out",
                    is_error=True, tool_call_id=tool_id,
                )
            # a2a 仍在 in-flight；让它们跑完或 cancel（见 §5.6）
            for t in a2a_tasks:
                if not t.done():
                    t.cancel()
            break

        await asyncio.sleep(0.1)

    steering_text = "\n\n".join(steering_parts) if steering_parts else None
    return _GatherResult(
        tool_results=tool_results,
        a2a_results=a2a_results,
        steering_text=steering_text,
    )
```

**关键点**：
1. A2A **不写进 `pending_tools`**——`tool_call_id` 与 `RunToolJob.job_id` 的命名空间不同，混在一起会误删。
2. steering 数量上限 `MAX_STEERING_PARTS = 16`——防止用户在 tool 执行期狂发消息导致 OOM / 单条 prompt 爆掉。
3. 超时分支把所有 in-flight tool / a2a 标 `is_error=True`，让 LLM 下一轮能看到错误。a2a 用 `task.cancel()` 中断。
4. 三种"完成"的退出条件都要正确判定：tool 全完 + a2a 全完 + steering 至少被认领过一轮（避免无限 spin）。

### 4.5 Tool Result User Message 组装

```python
def _append_tool_result_user_message(
    ctx: RunContext,
    gather: _GatherResult,
) -> None:
    """追加一条 user 消息：tool_result blocks 在前，steering 文本接在最后。

    LLM API 约束：assistant(tool_use) 必须紧跟 user(tool_result)。
    tool_result blocks 在先，steering 以 ``{"type":"text"}`` block 接在末尾。
    """
    blocks: list[dict] = []
    for tool_call_id, tr in gather.tool_results.items():
        blocks.append({
            "tool_use_id": tool_call_id,
            "type": "tool_result",
            "content": tr.content,
            "is_error": tr.is_error,
        })
    for tc_id, ar in gather.a2a_results.items():
        blocks.append({
            "tool_use_id": tc_id,
            "type": "tool_result",
            "content": (ar.response or {}).get("text", ""),
            "is_error": not ar.success,
        })
    if gather.steering_text:
        blocks.append({"type": "text", "text": gather.steering_text})

    ctx.messages.append({
        "role": "user",
        "content": gather.steering_text or "",
        "content_blocks": blocks,
    })
```

### 4.6 `_split_tools()` 与 A2A

```python
@dataclass
class _SplitJobs:
    tool_jobs: list[RunToolJob]
    a2a_jobs: list[SendA2AJob]

def _split_tools(
    self,
    ctx: RunContext,
    tool_uses: list[dict],
) -> _SplitJobs:
    """按 tool_name 把 LLM tool_use 分流到 tool_job_board / a2a_job_board。

    - ``message_magi`` → SendA2AJob → a2a_job_board
    - 其余 → RunToolJob → tool_job_board
    """
    tool_jobs: list[RunToolJob] = []
    a2a_jobs: list[SendA2AJob] = []

    for tu in tool_uses:
        name = tu.get("name")
        args = dict(tu.get("input") or {})
        if name == "message_magi":
            try:
                target_magic_id = int(args["magic_id"])
                text = str(args["text"])
                if target_magic_id <= 0 or not text.strip():
                    raise ValueError("magic_id and text are required")
            except (KeyError, TypeError, ValueError) as exc:
                # 校验失败当普通 tool 投递，input 加 _validation_error
                tool_jobs.append(RunToolJob(
                    run_id=ctx.run_id,
                    tool_call_id=str(tu["id"]),
                    tool_name="message_magi",
                    payload={"arguments": {"_validation_error": str(exc)}, "context": {
                        "workspace": self.workspace,
                        "uid": ctx.uid or 0,
                        "channel": ctx.channel,
                        "session_id": ctx.session_id or "",
                    }},
                ))
                continue
            a2a_jobs.append(SendA2AJob(
                run_id=ctx.run_id,
                tool_call_id=str(tu["id"]),
                target=str(target_magic_id),
                expect_reply=bool(args.get("expect_reply", False)),
                request={"text": text, "uid": ctx.uid, "session_id": ctx.session_id},
            ))
        else:
            tool_jobs.append(RunToolJob(
                run_id=ctx.run_id,
                tool_call_id=str(tu["id"]),
                tool_name=name,
                payload={"arguments": args, "context": {
                    "workspace": self.workspace,
                    "uid": ctx.uid or 0,
                    "channel": ctx.channel,
                    "session_id": ctx.session_id or "",
                }},
                catalog_revision=self.bus.tool_catalog_book.get().revision
                    if self.bus.tool_catalog_book.get() else 0,
                schema_hash=self._tool_schema_hash(name),
            ))

    return _SplitJobs(tool_jobs=tool_jobs, a2a_jobs=a2a_jobs)
```

A2A 在 `_gather_all` 中作为独立 task 处理（每个 SendA2AJob publish 完立刻 `asyncio.create_task(bus.a2a_job_board.get_result(key=...))`）；tool 与 a2a 的结果命名空间分开，永不混淆。

### 4.7 投递 `_publish_delivery`

回复文本走 delivery outbox，**不写进 `ChatJobResult`**。理由：

- **steering 场景下 N 个 ChatJob → 1 条 reply**：`ChatJobResult` 是 N:1 的关系中介，每个 job 都写自己的 result——但只有一条 reply。如果把 reply 塞进 `ChatJobResult`，steering job 的 publisher 要么拿到空 reply，要么拿到重复 reply。
- **职责分离**：`ChatJobResult` 表达"job 是否处理成功"，`delivery_job_board` 承载"回复投递到哪个 channel"。AgentWorker 负责生产 reply，DeliveryWorker 负责投递——这和 providers/tools worker 的分层一致。
- **Channel 端同步等待**：REST API channel 如果同步等 `get_result(key=job.event_id)`，拿回的是 `success/error_code`——它已经知道结果。具体 reply 内容通过 delivery 路径或 session 查询获取。

```python
def _commit_terminal_reply(self, ctx: RunContext) -> None:
    """一次事务完成 terminal state，而不是逐个 Book / Board 写入。"""
    self.turns.commit_terminal(
        run_id=ctx.run_id,
        event_id=ctx.root_event_id,
        assistant_text=ctx.final_reply,
        token_usage=ctx.pending_token_usage,
        delivery={
            "channel": ctx.channel,
            "destination": ctx.delivery_address,
            "payload": {"text": ctx.final_reply, "session_id": ctx.session_id,
                        "uid": ctx.uid},
        },
    )
```

该事务写 assistant transcript、delivery outbox、`AgentTurn.terminal` 与
`ChatJobResult`。DeliveryWorker 随后消费 outbox；崩溃恢复只能重试未完成的
delivery，不能重新推理或再次创建一条 assistant message。

### 4.8 标题生成 `_maybe_title`

```python
def _maybe_title(self, ctx: RunContext) -> None:
    """第一个 assistant turn 后异步生成会话标题。

    Phase 1 **暂不实现**——直接放个 TODO 日志，避免无错误吞掉。
    Phase 2 由 ``auto_title.py`` 迁到 new_bus 后接回。
    """
    logger.debug(
        "auto-title deferred to Phase 2; session=%s will keep default label",
        ctx.session_id,
    )
```

### 4.9 Token Usage 记录

```python
def _record_token_usage(self, ctx: RunContext, result: CallLLMResult) -> None:
    """每次成功的 LLM 调用落一条 token_usage_book。"""
    if not result.token_usage:
        return
    self.bus.token_usage_book.add(
        uid=ctx.uid or 0,
        provider=result.model.split(":")[0] if result.model else "unknown",
        model=result.model or "",
        usage=result.token_usage,
        run_id=ctx.session_key[1] or "",
    )
```

### 4.10 Settings 读取

```python
def _read_max_iterations(self) -> int:
    raw = self.bus.settings_book.get("system.tool_max_iterations")
    try:
        return int(raw) if raw else 10
    except (TypeError, ValueError):
        return 10

def _read_max_tokens(self) -> int:
    raw = self.bus.settings_book.get("system.max_tokens")
    try:
        return int(raw) if raw else 1024
    except (TypeError, ValueError):
        return 1024

def _read_tool_wait_seconds(self) -> float:
    raw = self.bus.settings_book.get("system.tool_wait_seconds")
    try:
        return float(raw) if raw else 300.0
    except (TypeError, ValueError):
        return 300.0

def _read_llm_timeout_seconds(self) -> float:
    raw = self.bus.settings_book.get("system.llm_timeout_seconds")
    try:
        return float(raw) if raw else 120.0
    except (TypeError, ValueError):
        return 120.0
```

---

## 5. Steering 设计

### 5.1 设计原则：board 即协调点

不需要进程内队列（`asyncio.Queue`）或 Event。`agent_job_board` 保存消息，
`AgentTurnBook` 保存该会话当前由谁执行、执行到哪一步；二者共同构成可恢复的
协调点。AgentWorker 只在自己持有该 turn lease 时，于 `_gather_all` 中认领
同会话的 steering。

### 5.2 数据流

```
Channel publishes ChatJob(conversation_id="abc", payload.text="再查一下")
        │
        ▼
AgentTurnStore sees active turn for "abc"
        │
        └─ message remains pending (never claimed then released)
        │
        │  (持有该 turn lease 的 _process("abc") 在 _gather_all 中运行)
        │
        ▼
_gather_all() calls agent_job_board.claim_for_conversation(conversation_id="abc")
        │
        ├─ CAS 认领到 ChatJob(payload.text="再查一下")
        ├─ steering_parts.append("再查一下")
        └─ 与下一次 `commit_waiting_effects()` 一并标记 consumed
        │
        ▼
tool 完成后 → _append_tool_result_user_message(steering_text="再查一下")
            → 文本接在 tool_result blocks 之后
            → 下一轮 LLM 调用看到 steering
```

### 5.3 注入时机

**steering 只在 tool_result user 消息末尾注入**。原因：

1. LLM API 约束：`assistant(tool_use)` → `user(tool_result)` 必须严格配对，中间不能插入 steering 文本。
2. `tool_result` 后的 user 消息末尾可以安全地追加 `{"type": "text", "text": steering}` content block。
3. 如果 LLM 返回无 tool_use（terminal），不会进入 `_gather_all`，也就不存在 tool_result 注入点。`commit_terminal()` 释放持久化 conversation lease；随后消息作为下一次 root turn 被认领。

### 5.4 多段 steering 的处理

`_gather_all` 的轮询循环中每次都尝试 `claim_for_conversation`。如果用户在 tool 执行期间连续发了多条消息，每一条都会被陆续认领并追加到 `steering_parts`，直到达到 `MAX_STEERING_PARTS = 16` 上限。最终拼接为一段文本（`"\n\n"` 分隔）。

### 5.5 与旧设计对比

| | 旧设计 (steer_queue + Event) | 新设计 (board claim) |
|---|---|---|
| RunContext 额外字段 | 2 (queue, event) | 0 |
| 协调机制 | 进程内 in-memory | SQLite board（天然持久化） |
| `_run()` 路由 | 注入到 context.queue | `AgentTurn` 持久化 ownership + scoped claim |
| `_process()` 感知 | 被动 Event.set() 通知 | 主动 `claim_for_conversation` |
| 代码量 | 多一个队列消费逻辑 | `claim_for_conversation` 复用现有 claim 模式 |
| 语义 | "注入" | "认领"——符合 board 哲学 |
| 崩溃恢复 | 队列丢失 | board 中 steering job 仍在 pending，下一个 run 继续认领 |
| 多实例并发 | 队列不共享，丢消息 | CAS + conversation lease 保证单 owner |

### 5.6 Cancel 处理

旧 bus 按 `run_id` 显式取消；新设计必须保持这个语义。取消 job 是
`ChatJob(kind="run.cancel", run_id=<target>)`，不能借用 `conversation_id` 广播。

- `request_cancel(run_id)` 以事务写入 `AgentTurn.cancel_requested_at`，并完成 cancel
  job；不存在或已 terminal 的 run 返回明确结果。
- Worker 在每次 publish、每个等待循环和每次 commit 前检查该标记；看到取消后不再
  创建新 effects，调用 `commit_terminal_cancelled()`。
- 已 claim 的 tool / A2A 不能被本地 `task.cancel()` 假装停止。需要 job board 的
  durable cancel-request 状态，消费者协作检查；已完成的结果也不得覆盖 cancelled
  terminal state。

```python
def request_cancel(self, *, run_id: str) -> bool:
    """持久化取消请求；返回是否成功转换了活动 turn。"""
    return self.turns.request_cancel(run_id=run_id)
```

进程内 Event 只可作为缩短轮询延迟的优化，不能承载取消真相。

---

## 6. 异常处理

| 场景 | 处理 | settings 来源 |
|---|---|---|
| LLM job 超时 | `commit_terminal_failure("llm_timeout")` 或按策略 retry | `system.llm_timeout_seconds` |
| LLM job 返回 `success=False` | 持久化错误并结束或 retry | — |
| Tool job 超时 | 超时 tool 标记为 `is_error=True, content="timed out"`，继续 loop | `system.tool_wait_seconds` |
| Tool job 返回 `success=False` | `is_error=True` 的 tool_result，LLM 下一轮可见错误 | — |
| Agent loop 超迭代上限 | `commit_terminal()` 写稳定提示和 outbox | `system.tool_max_iterations` |
| Cancel | `commit_terminal_cancelled()`；默认不制造伪造 assistant reply | — |
| 未知异常 (Exception) | `commit_terminal_failure("agent_crashed:<type>")` | — |

**关键原则**：AgentWorker 不因单个 job 崩溃；所有失败经持久化 terminal
transition 结束。对用户是否发送 fallback 必须是产品决策，不能把“写了
`ChatJobResult(success=False)`”与“必然发送一条回复”混为一谈。

---

## 7. 与 providers/tools worker 的关系

```
AgentWorker        │    LLM job board    │  ProvidersWorker
───────────────────┤                     ├─────────────────
publish            │ ──── CallLLMJob ──→ │ claim → execute
                   │                     │       → submit_result
wait_for_result()  │ ←─ CallLLMResult ── │

AgentWorker        │    Tool job board   │  ToolsWorker
───────────────────┤                     ├─────────────────
publish × N        │ ──── RunToolJob ──→ │ claim → execute
                   │                     │       → submit_result
_gather_all()      │ ←─ RunToolResult ── │

AgentWorker        │    A2A job board    │  A2AWorker (待实现)
───────────────────┤                     ├─────────────────
publish × M        │ ──── SendA2AJob ──→ │ claim → execute
                   │                     │       → submit_result
_gather_all()      │ ←─ SendA2AResult ── │

AgentWorker        │   agent_job_board   │  AgentWorker 自身
───────────────────┤                     ├─────────────────
claim_for_conversation()
                   │  ← 同会话新 ChatJob
submit_result()    │ → ChatJobResult    │ 完成当前 job
```

AgentWorker **不直接调用** `provider.chat()` / `tool.run()` / a2a runtime。所有跨 worker 通信通过 job board 的 publish → claim → submit_result 模式进行。`DeliveryWorker` 和 `A2AWorker` 是端到端 cutover 的前置条件：前者必须消费新的 delivery outbox；后者尚未实现时，`message_magi` 必须受 feature gate 保护并产生结构化 unavailable 结果，不能永久等待。

---

## 8. 实现计划

### Phase 0：冻结合同与 cutover 前置条件

1. 以当前 `ChatJob` / `ChatJobResult`、`RunToolJob`、`SendA2AJob` 为准，修正文档
   与生产者；任何 DTO 扩展单独迁移并配套测试。
2. 新增 `AgentTurnBook` / `AgentTurnStore`，定义 phase、conversation lease、
   cancel request 和原子 transition。
3. 给相关 job board 增加 ownership-aware `renew_lease()`；为 SQLite 实现 CAS claim，
   不使用 `SKIP LOCKED` 作为互斥保证。
4. 迁移或实现新的 DeliveryWorker；A2A worker 未就绪则显式 feature gate。

### Phase 1：可恢复核心 loop

**文件**：
- `magi/agent/worker.py` — AgentWorker 重写
- `magi/new_bus/guild/chatJob.py` — scoped CAS claim
- `magi/new_bus/guild/base.py` — lease renewal 与可验证的 cancel 状态转换
- `magi/new_bus/library/local/agentTurnBook.py`（或等价 runtime store）— turn 状态与原子 transition

**步骤**：

1. ✅ 构造注入 `NewBus`、`start/stop` 生命周期（已完成）
2. ⬜ 实现 `claim_root_and_acquire_turn()`、scoped steering claim、lease heartbeat。
3. ⬜ 实现 `commit_waiting_effects()` 与 `commit_terminal()`，并让 transcript / outbox
   与状态结果同一事务提交。
4. ⬜ 实现 `_process()`、`_gather_all()` 和基于 `run_id` 的取消检查。
5. ⬜ 实现 `_split_tools()`（`message_magi` → A2A；其余 → Tool），每个 effect 使用
   稳定的 run / tool-call idempotency key。
6. ⬜ 实现 `_build_llm_job()`、历史加载、tool schemas、硬 context 上限、token usage。
7. ⬜ 使用真实 NewBus DeliveryWorker 完成无-tool 端到端路径；A2A 未就绪时明确拒绝。
8. ⬜ 删除旧 placeholder DTO 与 stub 方法；记录受影响的 Agent 测试，**不在本
   阶段运行或修补为“中间态通过”**。

### Phase 2: 子模块迁移

按子模块独立迁移到 `NewBus` 构造注入；每个子模块迁完，去掉 worker 里的内联占位：

| 文件 | 改动 | 关联到 worker |
|---|---|---|
| `agent_context.py` | `get_bus().session` → `bus.sessions_book`；`get_bus().tool_catalog` → `bus.tool_definitions_book` | `_load_history`、`_tool_schemas` |
| `system_prompt.py` | `get_bus().memory/contacts/settings/magic` → 对应 new_bus Books | `_system_prompt` 删除内联，改为调用 `build_system_prompt` |
| `compaction.py` | `get_bus().settings/session` → new_bus Books；`enqueue_llm_job` → `llm_job_board` | `_build_llm_job` 前置 compact |
| `auto_title.py` | `get_bus().session` + `enqueue_llm_job` → new_bus | `_maybe_title` 接回真实实现 |
| `instructions.py` | `get_bus().magic` → MAGIS Books | `_system_prompt` 块 2 |
| `token_usage.py` | `get_bus().token_usage` → `token_usage_book` | `_record_token_usage` 可委托 |

**额外**：`_system_prompt` 块 4/5 需要给 `ContactBook` 加 `get_by_uid` / `list_notes_for_uid` / `read_daily_note_for_uid`；`SettingsBook` 加 `compaction_policy()` / `show_daily_note()` / `show_daily_note_prompt()`；`MembershipBook` 加 `instruction_context(magic_id)`。这些是 Book 层适配，但与 Agent / Channel cutover 作为同一迁移交付，不以局部测试通过作为完成信号。

### Phase 3: Channel 端

Channel (TG/API/tasks/A2A ingress) 端按 §2.1 publish `ChatJob` 到
`agent_job_board`，并改从 committed transcript / run status 读取结果。旧 Bus
的输入与 delivery 路径在所有生产者、消费者均已迁移后删除；删除本身属于完整实施，
随后才进入统一测试。

### Phase 4：统一验证与清理

仅当 Phase 0–3 全部完成、旧 Bus 的 Agent / Channel 路径已删除后，才集中更新并运行
§10 的全部测试。不要在中间阶段为了让旧测试通过而恢复兼容层、保留双写，或把临时
实现当作完成状态。

---

## 9. 未覆盖项 / 已知限制

以下特性由后续步骤单独处理，**Phase 1 不实现**：

- **Compaction**：完整摘要压缩留待 Phase 2；Phase 1 必须已有硬 context 上限和
  可观测的降级结果，不能接受无界内存或上下文溢出。
- **流式输出**：`bus.stream_hub` 已就绪，但 AgentWorker 暂不消费流式（`streaming=False`）。用户体验上等同于老 bus 的非流式路径。
- **Auto title**：Phase 1 仅日志；新会话首次对话无标题。
- **跨 channel conversation identity**：由 Channel 在发布时定义稳定 conversation id；是否将
  channel 纳入 identity 是协议决定，不能在 Worker 内临时猜测。
- **Delivery**：新 DeliveryWorker 是 Phase 0 前置条件，不是运行时假设。
- **A2A**：没有新 A2A worker 时不得进入等待路径；必须 feature gate 或返回可见错误。

---

## 10. 最终统一测试策略

本节的测试**只在 Phase 0–3 全部实施完成后运行**。迁移过程中可以做
`git diff --check`、静态导入检查或语法检查以发现明显错误，但这些不是功能验收，
也不运行局部单元、集成或端到端测试。mock 单元测试不能替代真实 SQLite 的事务与
lease 验证：

1. **单元：单 ChatJob → 单轮 LLM → 无 tool → 投递**
   - mock `llm_job_board.get_result` 返回固定 CallLLMResult
   - 断言：一次 terminal transition 同时写入 assistant transcript、outbox 和
     `ChatJobResult(success=True)`

2. **单元：单 ChatJob → LLM 返回 tool_use → tool 完成 → 第二轮 LLM → 投递**
   - mock tool_job_board publish + get_result
   - 断言：第二轮 `_build_llm_job` 的 messages 包含 ordered tool_result user message，
     且重启后不会再次发布同一个 tool call

3. **单元：steering 注入**
   - 第一轮 LLM 返回 tool_use，进入 `_gather_all`
   - 同时 publish 一条同 `conversation_id` 的 ChatJob
   - 断言：第二轮 messages 末尾追加了 steering text；steering ChatJob 在同一次
     transition 中被标记 consumed

4. **单元：cancel 路径**
   - 以 `run_id` 发布 `kind=run.cancel`
   - 断言：活动 turn 持久化为 cancel-requested / cancelled；晚到的 Tool/A2A result
     不能覆盖 terminal 状态

5. **集成：内存 SQLite 真 board**
   - 用 `bootstrap_new_bus(state_dir=tmp_path)` 真起 board
   - publish 真实 ChatJob，跑 `_process`，断言 ORM 行状态正确

6. **崩溃恢复与幂等**：在“LLM 返回后、effects 提交前”“effects 提交后、结果
   提交前”“terminal commit 后、delivery 完成前”分别强制终止，重启后断言不重复
   LLM / tool side effect、不遗漏 transcript、delivery 可重试。

7. **租约与并发**：LLM / tool 等待超过默认 lease 时仍只有一个 owner；两个 Worker
   并发争抢同一 conversation 时只有一个 root turn，第二条消息作为 steering。

8. **边界与 e2e**：真实 NewBus + DeliveryWorker 跑通 WebUI / Telegram 的一个输入到
   committed reply；A2A 未启用时 `message_magi` 返回稳定 unavailable 错误。

---

## 11. 多 Agent 协作开发约定

本计划书由 user 分配给多个 Agent 在同一时间窗内并行实现。任何 Agent
在动手前**必须**读完本节；执行期间违反任一条，等同于打断同事工作，
需要回滚 + 道歉。

### 11.1 不要触碰 git

- **禁止**：`git commit` / `git push` / `git rebase` / `git reset` / `git stash` /
  `git checkout -- <file>` / `git restore` / `git clean` / `git branch -D` 任意一项。
- **理由**：多 Agent 在同一份代码上并发写，git 操作会丢别人写到一半的改动。
  阶段性 commit 由 user 在确认全员对齐后统一执行。
- **可以**：`git status` / `git diff` / `git log` —— 只读，用于核对"这块被人动过了吗"。
- **冲突信号**：发现 `git status` 输出"别人改了我也要改的文件"时，先停下来读对方
  的实现（见 §11.2），不要直接 `git checkout --` 把对方覆盖。

### 11.2 看到别的 Agent 在做，先读、再判断、再决定

发现实现冲突 / 重叠 / 风格不同时，按以下顺序处理：

1. **先读对方写的代码 + 注释**。对方在 commit message / 代码注释 / 设计书
   的 `§11.x` 同步块（见 §11.4）里会写明"我在做什么、为什么"。
2. **评估是否更好**：对方思路如果更贴近本设计书、或者解决了你没想到的边界，
   **保留对方的实现**，把自己的实现吸收进去（合并 / 替换局部变量名 / 让步风格）。
3. **如果你坚持自己更对**：在文件里**先写一段注释**说明分歧点和你的理由，
   让对方也能看到，再动代码。**不要**静默覆盖。
4. **如果你觉得对方写得确实烂**：可以介入重写，但必须满足三件事——
   - 在原文件留一段注释引用本节 §11.2，标明"原实现由 X 写入于 Y，原因 Z 被
     重写为 W"，给原作者留出反应窗口。
   - 在本设计书 §11.4 同步块写一行改动条目。
   - 如果改动面较大（超过 30 行 / 跨多个文件），先停下来 ping user 仲裁，不要
     单方面推平。

### 11.3 写代码时给同事看的注释

多 Agent 并行写代码，"对方能不能看懂"和"我能不能维护"同等重要。每条
规则都要**用注释表达出来**，不只是 commit message：

- **做什么**：`# [agent-name, YYYY-MM-DD] 用途：一句话`
- **为什么这样写**：如果偏离设计书、或者做了 trade-off，必须解释。
- **未完成项**：用 `# TODO(<agent-name>): ...` 标注，并**写明下一步**。
  不能只写 "TODO" 一词。
- **与别人交接的边界**：方法签名 / 模块边界 / 跨文件调用，写清"这是新接口，
  下游需要 N 处的 M 改动"。

注释风格参考本仓库已有约定：中文为主，技术名词用英文；不要无意义的
"fix bug" / "improve"。

### 11.4 设计书同步块

本节是 **Agent 之间的实时协调面板**，禁止 user 之外的人修改格式。
任何 Agent 写完代码 / 发现设计书需要补丁 / 想与同事握手，必须在本节
追加一行，按时间倒序排列（最新在最上）。

格式：

```
- [agent-name, YYYY-MM-DD HH:MM] <动作> <文件 / §引用>
  原因：<一句话>
  影响：<自己 / 同事 / user>
  需要对方回复：<是 / 否 + 期望方式>
```

**示例**：

```
- [claude-A, 2026-08-08 14:30] 已完成 BaseJobBoard.release() / cancel()
  文件：magi/new_bus/guild/base.py
  原因：按 §2.4 设计
  影响：tools / providers worker 后续可复用
  需要对方回复：否（接口稳定，未锁定的 chatJobBoard.claim_for_conversation
    还在 claude-B 写，预计 14:45 完成）

- [claude-B, 2026-08-08 14:15] 正在写 chatJobBoard.claim_for_conversation
  文件：magi/new_bus/guild/chatJob.py
  原因：按 §2.5 设计
  影响：claim_for_conversation 是 §3.1 _run() 的前置依赖
  需要对方回复：否（接口草案中，预计 14:40 完成时回填签名）

- [claude, 2026-08-08] 计划：Round 1 — 在 BaseJobBoard 加 `renew_lease()` + `cancel()`
  文件：magi/new_bus/guild/base.py
  原因：§2.6 lease 设计；§8 Phase 0 step 3；为 AgentTurnStore 的 lease heartbeat 与
  worker 取消路径提供原语。`release()` 当前实现将被 §2.6 明确禁止，标注 deprecate。
  影响：所有继承 BaseJobBoard 的 board 都自动获得新方法；与现有 release() 不冲突。
  需要对方回复：否（接口草案见下方 "Round 1 提案"，等 user 点头再动笔）。
```

### 11.8 Round 1 提案：BaseJobBoard.renew_lease() + cancel()

```python
# 新增到 BaseJobBoard

def renew_lease(
    self,
    *,
    key: str,
    owner: str,
    extend_seconds: int | None = None,
) -> bool:
    """CAS 续租；只有 leased_by == owner 才能成功。
    返回 True 表示续租成功；False 表示 ownership 已被回收（worker 必须放弃）。"""
    extend = timedelta(seconds=extend_seconds or self._lease_seconds)
    now = utcnow_naive()
    with self._session() as s:
        row = s.scalar(
            select(self.job_model).where(
                getattr(self.job_model, self.natural_key_attr) == key,
                self.job_model.leased_by == owner,
                self.job_model.status == "processing",
            )
        )
        if row is None:
            return False
        row.leased_until = now + extend
        s.commit()
        return True

def cancel(
    self,
    *,
    key: str,
    owner: str,
) -> bool:
    """Ownership-aware cancel：把 leased_until 缩到 now()，让 worker 在下一次
    renew 之前感知到自己失去 ownership；status 保持 'processing' 以便
    后续 reclaim。返回 True 表示 cancel 成功。"""
    now = utcnow_naive()
    with self._session() as s:
        row = s.scalar(
            select(self.job_model).where(
                getattr(self.job_model, self.natural_key_attr) == key,
                self.job_model.leased_by == owner,
                self.job_model.status == "processing",
            )
        )
        if row is None:
            return False
        row.leased_until = now  # 立即过期
        s.commit()
        return True
```

`release()` 标 `.. deprecated::` 注释，保留给已迁移的 caller 暂时不掉；Phase 4 统一删除。

### 11.5 抢占式声明（防重复造轮子）

在准备写一个跨文件 / 跨模块的改动之前，**先在本节追加一行"正在写 X"**，
作为软性声明。规则：

- 声明后 30 分钟内没人反对 / 没出现完成行，默认独占。
- 如果两个 Agent 同时声明同一个组件，**先到先得**（按本节时间戳）。
  后到者改做集成测试 / 文档 / 上游 caller 适配。
- 完成后把"正在写"改成"已完成"，并在同一条目里更新状态。

### 11.6 找 user 仲裁的触发条件

以下场景**必须**停下来 ping user，不要私下决定：

- 设计书 §1-§10 的语义被推翻（例如发现某条规则在 new_bus 上做不到）。
- 跨 PR 合并冲突（同文件同一区域被双方改了一半）。
- §11.2 评估后**双方都坚持自己更对**——user 来定。
- 测试出现"两个 Agent 的代码分别通过、合并后失败"——通常是状态机语义
  没对齐，先停。

### 11.7 收尾约定

每个 Agent 在结束自己本次任务时，**最后**做这三件事：

1. 把本节里的"正在写 X"改成"已完成 X"；如有分歧，写明未决项。
2. 在自己改过的代码文件顶部留一段 1-3 行的"本次改动摘要"，便于 user
   后续审查时快速定位。
3. 不要 `git add` / 不要 `git commit`——user 会统一收尾。

---

## 附录 A: 新增 / 修改的方法清单

| 模块 | 改动 |
|---|---|
| `magi/new_bus/guild/base.py` | 新增 ownership-aware `renew_lease()` 与 cancel 状态转换 |
| `magi/new_bus/guild/chatJob.py` | 新增 scoped CAS `claim_for_conversation(conversation_id)` |
| `magi/new_bus/library/local/agentTurnBook.py` | 新增 turn state、conversation lease 和原子 transition |
| `magi/new_bus/bootstrap.py` | 明确 `agent_job_board` 的底层 `chatJobBoard` 实现与实例装配 |
| `magi/agent/worker.py` | 重写 AgentWorker（按本设计 §3、§4） |

---

## 附录 B: 与旧 bus 的字段映射

| 旧 bus 用法 | new_bus 对应 |
|---|---|
| `BusStore.publish_agent_message(AgentMessage)` | `agent_job_board.publish(ChatJob(payload=...))` |
| `BusStore.claim_next_agent_message(worker_id)` | `AgentTurnStore.claim_root_and_acquire_turn()` |
| `BusStore.commit_agent_transition(event_id, ...)` | `AgentTurnStore.commit_waiting_effects()` / `commit_terminal()` |
| `BusStore.fail_agent_message(event_id, ...)` | `AgentTurnStore.commit_terminal_failure()` |
| `BusStore.complete_agent_input(event_id)` | `commit_terminal()` 内原子完成 `ChatJobResult` |
| `BusStore.load_tool_continuation(run_id)` | `AgentTurnBook` 持久化 phase、messages 与 pending effects |
| `BusStore.pending_steering_inputs(run_id)` | `agent_job_board.claim_for_conversation(conversation_id)` |
| `BusStore.recover_expired_leases()` | board / AgentTurn 的 lease reclaim + ownership 检查 |
| `BusStore.is_run_within_deadline(run_id)` | `AgentTurnStore` 在每次 transition 前读取 payload 的 `deadline_at` |
| `BusStore.enqueue_llm_job(...)` | `llm_job_board.publish(CallLLMJob(...))` |
| `BusStore.load_llm_job_result(attempt_id, ...)` | `llm_job_board.get_result(key=attempt_id)` |
| `BusStore.enqueue_tool_job(...)` | `tool_job_board.publish(RunToolJob(...))` |
| `BusStore.enqueue_delivery(...)` | `delivery_job_board.publish(DeliveryJob(...))` |
| `BusStore.publish_agent_message(synth)` 给自己塞 provider.completed | 删除该机制——ProviderWorker 完成后直接把结果写到 `llm_job` 的 result row，AgentWorker 通过 `get_result` 读到，无需自循环 inbox |
| `Bus.session.get(uid, session_id)` | `sessions_book.get_for_owner(uid, session_id)` |
| `Bus.session.set_title_if_null(...)` | `sessions_book.set_title_if_null(...)`（Phase 2 由 ContactBook/SessionBook 适配补齐） |
| `Bus.session.replace_compacted(sess, ...)` | 直接用 `messages_book.archive()` + `messages_book.add()` + `sessions_book.touch()`（Phase 2） |
| `Bus.contacts.get(uid)` | `contacts_book.get_by_uid(uid)`（Phase 2 适配补齐） |
| `Bus.contacts.list_notes(uid)` | `contact_notes_book.list_for_contact(uid)`（Phase 2 适配补齐） |
| `Bus.contacts.read_daily_note(uid)` | `contacts_book.read_daily_note(uid)`（Phase 2 适配补齐） |
| `Bus.memory.list_for_owner(uid)` | `memory_book.list_by_owner(uid)` |
| `Bus.token_usage.record(...)` | `token_usage_book.add(...)` |
| `Bus.magic.instruction_context()` | `memberships_book.instruction_context(magic_id)`（Phase 2 适配补齐） |
| `Bus.settings.compaction_policy()` | `settings_book.compaction_policy()`（Phase 2 适配补齐） |
| `Bus.tool_catalog.list_schemas(caller_role=...)` | `tool_definitions_book.list_schemas(caller_role=..., caller_admin=False)` |
| `StreamHub.publish(StreamEvent(...))` | `stream_hub.create(key) / get(key) / close(key)`（Phase 1 不消费） |
