# AgentWorker 迁移到 new_bus 设计书

## 状态

**当前阶段**：骨架就位，stub 待填。

`magi/agent/worker.py` 已完成第一轮重构（构造注入 `NewBus`、去掉 `magi.bus` 依赖），所有旧 `magi.bus` store 调用已替换为 `raise NotImplementedError(...)` 占位 stub。`magi/startup/runtime.py` 已改为 `start_agent_worker(bus=new_bus)`。其余 agent 子模块（`agent_context.py`、`system_prompt.py`、`compaction.py`、`auto_title.py` 等）仍走老 `magi.bus`，按"以后再修"策略暂不动。

本设计书覆盖 `worker.py` 的下一步实现 + Phase 2 子模块迁移的边界。

---

## 1. 架构总览

```
                          ┌──────────────┐
   Channel (TG/API) ────→│ chat_job_board│  (ChatJob on chat_jobs)
                          └──────┬───────┘
                                 │ claim / claim_for_conversation
                          ┌──────▼───────┐
                          │  AgentWorker  │
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
                          │ chat_job_board│  (ChatJobResult)
                          └──────────────┘
```

**AgentWorker 只依赖 `NewBus`**。所有外部操作（LLM 调用、工具执行、消息投递、同会话 steering）通过对应的 job board 进行。AgentWorker 自身作为协调者，负责：

1. 从 `chat_job_board` 认领 `ChatJob`
2. 驱动 agent loop（上下文组装 → LLM 推理 → 结果分发）
3. 提交 `ChatJobResult` 完成本轮

**为什么选 `chat_job_board` 而不是 `agent_job_board`**：

- `chat_jobs` schema 自带 `text / channel / conversation_id / reply / metadata`，与"channel 消息触发 agent turn"的语义最贴。
- `agent_inbox`（`agent_job_board` 背后的表）的 schema 偏向 run 协调（`run_id / kind / payload / context_seq`），把 chat message 塞进 `payload` JSON 反而绕。
- 两条 board 的 API 形态完全一致（都是 `BaseJobBoard` 子类），切换只改字段引用，不引入新概念。

---

## 2. 核心数据结构

### 2.1 RunContext

单次 `ChatJob` 引发的完整 agent run 的全部内存状态。一次 `_process()` 调用对应一个 `RunContext` 实例。

```python
@dataclass
class RunContext:
    """Single ChatJob → agent run. All mutable state lives here."""

    # identity (from ChatJob.metadata)
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
```

**lifecycle**：由 `AgentWorker._run()` 在 claim 到 `ChatJob` 后构造，`_process()` 结束后销毁。

**与上版对比**：不再持有 `steer_queue` / `steer_event`——steering 通过 `chat_job_board.claim_for_conversation(conversation_id)` 直接从 board 认领，board 自身是唯一的状态协调点。

### 2.2 AgentWorker

```python
class AgentWorker:
    bus: NewBus                       # 构造注入
    poll_seconds: float = 0.25

    _task: asyncio.Task | None        # 主循环 task
    _stopping: bool                   # 退出信号
    _active_sessions: set[tuple[int | None, str]]  # (uid, conv_id) 防重复 run
```

**与上版对比**：`_active: dict[str, RunContext]` 简化为 `_active_sessions: set[tuple[int | None, str]]`——只用 key 占位防重复，不再往 `RunContext` 内部塞队列 / event。

### 2.3 chatJobBoard 扩展

```python
# magi/new_bus/guild/chatJob.py

class chatJobBoard(BaseJobBoard[_ChatJobRow, ChatJob, ChatJobResult]):
    # ... existing ...

    def claim_for_conversation(self, *, conversation_id: str) -> ChatJob | None:
        """Steering-scoped claim — 认领同会话的 pending ChatJob。

        与 ``claim()`` 行为一致，但不退回最旧的全局 pending 行；
        只在该 ``conversation_id`` 下查找。租约超时回收复用 ``BaseJobBoard``
        的标准逻辑（attempts ≥ MAX_ATTEMPTS 自动失败）。

        用法：AgentWorker 在 ``_gather_all`` 中每轮轮询调用一次，认领到
        的 ChatJob 立即 ``submit_result(success=True)`` 标记为
        consumed，不再触发独立 run，文本作为 steering 拼入下一轮 prompt。
        """
        with self._session() as s:
            now = utcnow_naive()
            lease_until = now + timedelta(seconds=self._lease_seconds)
            row = s.scalar(
                select(_ChatJobRow)
                .where(
                    _ChatJobRow.conversation_id == conversation_id,
                    or_(
                        _ChatJobRow.status == "pending",
                        and_(
                            _ChatJobRow.status == "processing",
                            _ChatJobRow.leased_until < now,
                        ),
                    ),
                )
                .order_by(_ChatJobRow.created_at, _ChatJobRow.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            if row.status == "processing" and row.attempts >= MAX_ATTEMPTS:
                exhausted = self._make_exhausted_result(row)
                self._submit(s, key=self._key_of(row), result=exhausted)
                s.flush()
                return None
            row.status = "processing"
            row.leased_until = lease_until
            row.attempts += 1
            if row.started_at is None:
                row.started_at = now
            s.commit()
            return _row_to_job(row, ChatJob)
```

### 2.4 BaseJobBoard 新增 `release()`

`_run` 在"已存在活跃 run"分支要把 claim 出来的 job 退回 pending。直接调私有 `_session()` 是反模式；统一给 `BaseJobBoard` 加公开方法：

```python
# magi/new_bus/guild/base.py

class BaseJobBoard(...):

    def release(self, *, key: str, decrement_attempts: bool = True) -> None:
        """释放已 claim 的 job，退回 pending 给后续 claim 流程。

        ``decrement_attempts=True`` 时把 attempts 减一（典型场景：
        AgentWorker 主动放弃一个 claim 是因为决定改走另一条路径，
        不应该消耗重试次数）；``False`` 时保留 attempts（典型：
        外部系统要放弃这个 job）。
        """
        with self._session() as s:
            row = s.scalar(
                select(self.job_model).where(
                    getattr(self.job_model, self.natural_key_attr) == key
                )
            )
            if row is None:
                return
            row.status = "pending"
            row.leased_by = None
            row.leased_until = None
            if decrement_attempts and row.attempts > 0:
                row.attempts -= 1
            s.commit()
```

这把"退回 pending"语义正式化，tools / providers worker 也能复用（"我 claim 了但不想执行"是合法场景，比如：缓存命中、shut-down 收尾）。

---

## 3. 主流程

### 3.1 `_run()` — 主循环

```python
async def _run(self):
    while not self._stopping:
        job = await asyncio.to_thread(self.bus.chat_job_board.claim)
        if job is None:
            await asyncio.sleep(self.poll_seconds)
            continue

        # 1. cancel kind — 不进 _process，直接完成
        if (job.metadata or {}).get("kind") == "run.cancel":
            self.bus.chat_job_board.submit_result(
                key=job.job_id,
                result=ChatJobResult(
                    job_id=job.job_id, success=True,
                ),
            )
            # 转发 cancel 到所有 in-flight run（见 §5.6）
            self._broadcast_cancel(job.conversation_id)
            continue

        uid = (job.metadata or {}).get("uid")
        session_key = (uid, job.conversation_id or "")

        # 2. 同 (uid, conv) 已有活跃 run → 退回 pending，
        #    由 _process 通过 claim_for_conversation 认领为 steering
        if session_key in self._active_sessions:
            self.bus.chat_job_board.release(key=job.job_id)
            continue

        self._active_sessions.add(session_key)
        ctx = RunContext(
            uid=uid,
            session_id=(job.metadata or {}).get("session_id"),
            channel=job.channel or "",
            caller_role=(job.metadata or {}).get("caller_role"),
            conversation_id=job.conversation_id or "",
            session_key=session_key,
            messages=[],
            max_iterations=self._read_max_iterations(),
        )
        try:
            await self._process(ctx)
        except Exception as exc:
            logger.exception("agent run failed conv=%s", ctx.conversation_id)
            ctx.final_error = f"agent_crashed:{type(exc).__name__}"
            ctx.final_reply = ctx.final_reply or self._fallback_reply()
        finally:
            self._active_sessions.discard(session_key)
            self.bus.chat_job_board.submit_result(
                key=job.job_id,
                result=ChatJobResult(
                    job_id=job.job_id,
                    success=ctx.final_error is None,
                    error_code=ctx.final_error,
                ),
            )
```

**关键点**：
- `cancel` kind 直接在 `_run` 完成，不进入 `_process`，避免 cancel 路径污染主 loop。
- `_active_sessions` key 升级到 `(uid, conversation_id)`——一个 `(uid, conv)` 同一时刻最多一个 run。group chat 等"多 channel 同 conv"的边界情况见 §9。
- 异常分支**必须**写 `ctx.final_error`，否则 `submit_result(success=True)` 会在出错时让 channel 端误以为成功。
- `ChatJobResult` **不承载回复文本**——`ChatJobResult` 只表示"这个 job 处理完毕"，回复统一由 `_publish_delivery()` 走 `delivery_job_board` 投递。steering 场景下多个 ChatJob 共享一条 reply，`ChatJobResult` 不能 1:1 绑定 reply（见 §4.7）。
- `cancel` 不通过 `_process` 处理；通过 `_broadcast_cancel` 通知 in-flight run（§5.6）。

### 3.2 `_process(ctx)` — agent loop

```
┌─ _load_history(ctx) ──────────────────┐
│ 从 sessions_book + messages_book 加载   │
│ 会话历史，追加本次 ChatJob.text         │
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
     │  3. ctx.messages.append(   │
     │       assistant message)   │
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
     │  6. publish tool + A2A     │
     │     jobs                  │
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
     _publish_delivery()
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

**compaction**（后续补齐）：在 `_build_llm_job` 调用前，先通过 `maybe_compact()` 检查 `ctx.messages` 的 token 估计值，若超过阈值则走 `llm_job_board` 生成摘要并原地裁剪 `ctx.messages`。当前子模块 `compaction.py` 仍走老 `magi.bus`，本步骤暂跳过——意味着 Phase 1 长会话会 OOM，**Phase 1 验收清单必含该项**。

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
                self.bus.chat_job_board.claim_for_conversation,
                conversation_id=ctx.conversation_id,
            )
            if steer is not None:
                text = (steer.metadata or {}).get("text") or ""
                if text:
                    steering_parts.append(text)
                # 立即完成，标记为 consumed（不单独发 delivery）
                self.bus.chat_job_board.submit_result(
                    key=steer.job_id,
                    result=ChatJobResult(
                        job_id=steer.job_id,
                        success=True,
                    ),
                )

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
                    job_id="",  # assigned by publish
                    run_id=ctx.session_key[1] or "",
                    tool_call_id=str(tu["id"]),
                    tool_name="message_magi",
                    arguments={"_validation_error": str(exc)},
                    payload={"context": {
                        "workspace": "",
                        "uid": ctx.uid or 0,
                        "channel": ctx.channel,
                        "session_id": ctx.session_id or "",
                    }},
                ))
                continue
            a2a_jobs.append(SendA2AJob(
                job_id="",
                run_id=ctx.session_key[1] or "",
                tool_call_id=str(tu["id"]),
                target_magic_id=target_magic_id,
                text=text,
                expect_reply=bool(args.get("expect_reply", False)),
            ))
        else:
            tool_jobs.append(RunToolJob(
                job_id="",
                run_id=ctx.session_key[1] or "",
                tool_call_id=str(tu["id"]),
                tool_name=name,
                arguments=args,
                payload={"context": {
                    "workspace": "",  # 来自 startup.paths.resolve_workspace_dir()
                    "uid": ctx.uid or 0,
                    "channel": ctx.channel,
                    "session_id": ctx.session_id or "",
                }},
                catalog_revision=self.bus.tool_catalog_book.get().revision
                    if self.bus.tool_catalog_book.get() else 0,
                schema_hash="",  # 由 tools worker 校验
            ))

    return _SplitJobs(tool_jobs=tool_jobs, a2a_jobs=a2a_jobs)
```

A2A 在 `_gather_all` 中作为独立 task 处理（每个 SendA2AJob publish 完立刻 `asyncio.create_task(bus.a2a_job_board.get_result(key=...))`）；tool 与 a2a 的结果命名空间分开，永不混淆。

### 4.7 投递 `_publish_delivery`

回复文本走 `delivery_job_board`，**不写进 `ChatJobResult`**。理由：

- **steering 场景下 N 个 ChatJob → 1 条 reply**：`ChatJobResult` 是 N:1 的关系中介，每个 job 都写自己的 result——但只有一条 reply。如果把 reply 塞进 `ChatJobResult`，steering job 的 publisher 要么拿到空 reply，要么拿到重复 reply。
- **职责分离**：`ChatJobResult` 表达"job 是否处理成功"，`delivery_job_board` 承载"回复投递到哪个 channel"。AgentWorker 负责生产 reply，DeliveryWorker 负责投递——这和 providers/tools worker 的分层一致。
- **Channel 端同步等待**：REST API channel 如果同步等 `get_result(key=job.job_id)`，拿回的是 `success/error_code`——它已经知道结果。具体 reply 内容通过 delivery 路径或 session 查询获取。

```python
def _publish_delivery(self, ctx: RunContext) -> None:
    """将回复发布到 delivery_job_board，由 DeliveryWorker 投递。

    每个 agent run 最多调用一次（terminal 分支 / max_iter 耗尽 / cancel）。
    steering ChatJob 不单独发 delivery。

    destination 留 None——DeliveryWorker 在 claim 时会从
    ``ctx.uid + ctx.session_id`` 反查 ``sessions_book.get_for_owner`` 拿
    ``delivery_address``（旧 bus 的 ``_delivery_destination`` 同样的功能）。
    前提是 DeliveryWorker 已经迁到 new_bus 并实现该反查路径。
    """
    self.bus.delivery_job_board.publish(DeliveryJob(
        channel=ctx.channel,
        payload={
            "text": ctx.final_reply,
            "session_id": ctx.session_id,
            "uid": ctx.uid,
        },
        destination=None,
    ))
```

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

不需要进程内队列（`asyncio.Queue`）或 Event，`chat_job_board` 本身是持久化的状态协调点。AgentWorker 通过在 `_gather_all` 中主动 `claim_for_conversation` 来认领同会话的新消息。

### 5.2 数据流

```
Channel publishes ChatJob(conv_id="abc", text="再查一下")
        │
        ▼
AgentWorker._run()  calls claim()
        │
        ├─ ("abc" in active_sessions?)  YES
        │
        ├─ bus.chat_job_board.release(job.job_id)
        │   ← 退回 pending，attempts -1
        └─ continue
        │
        │  (与此同时，_process("abc") 在 _gather_all 中运行)
        │
        ▼
_gather_all() calls claim_for_conversation(conversation_id="abc")
        │
        ├─ 认领到 ChatJob(text="再查一下")
        ├─ steering_parts.append("再查一下")
        └─ submit_result(success=True)  ← consumed by steering
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
3. 如果 LLM 返回无 tool_use（terminal），不会进入 `_gather_all`，也就不存在 tool_result 注入点。此时 `_active_sessions` 中的记录会在 `_process` 结束后清除，用户的下一条消息会作为正常 ChatJob 在下一轮 `_run()` 中被 claim。

### 5.4 多段 steering 的处理

`_gather_all` 的轮询循环中每次都尝试 `claim_for_conversation`。如果用户在 tool 执行期间连续发了多条消息，每一条都会被陆续认领并追加到 `steering_parts`，直到达到 `MAX_STEERING_PARTS = 16` 上限。最终拼接为一段文本（`"\n\n"` 分隔）。

### 5.5 与旧设计对比

| | 旧设计 (steer_queue + Event) | 新设计 (board claim) |
|---|---|---|
| RunContext 额外字段 | 2 (queue, event) | 0 |
| 协调机制 | 进程内 in-memory | SQLite board（天然持久化） |
| `_run()` 路由 | 注入到 context.queue | `release()` 退回 pending |
| `_process()` 感知 | 被动 Event.set() 通知 | 主动 `claim_for_conversation` |
| 代码量 | 多一个队列消费逻辑 | `claim_for_conversation` 复用现有 claim 模式 |
| 语义 | "注入" | "认领"——符合 board 哲学 |
| 崩溃恢复 | 队列丢失 | board 中 steering job 仍在 pending，下一个 run 继续认领 |
| 多实例并发 | 队列不共享，丢消息 | SQLite 锁保证只有一个 worker 拿到 |

### 5.6 Cancel 处理

旧 bus 有 `AgentMessage.kind == "run.cancel"` 路径；新 bus 通过 `ChatJob.metadata.kind == "run.cancel"`。

- **claim 到 cancel**：`_run` 直接 `submit_result(success=True)`，**不进入 `_process`**。
- **影响 in-flight run**：通过 `self._in_flight: dict[session_key, asyncio.Event]` 通知。`RunContext` 增加 `cancel_event`，`_process` 的循环开头检查；`_gather_all` 也检查以提前中断。
- **in-flight tool / a2a**：cancel 触发后调用 `bus.tool_job_board.cancel(key=...)` 与 `bus.a2a_job_board.cancel(key=...)`（这两个 cancel 方法需要扩展 `BaseJobBoard`）；不依赖 worker 协作停止。

```python
def _broadcast_cancel(self, conversation_id: str) -> None:
    """通知 in-flight run 中断。"""
    for key, event in self._in_flight.items():
        if key[1] == conversation_id:
            event.set()
```

`_in_flight` 在 `_process` 入口 `set`，出口 `discard`。

---

## 6. 异常处理

| 场景 | 处理 | settings 来源 |
|---|---|---|
| LLM job 超时 | `ctx.final_error = "llm_timeout"`，`_publish_delivery` | `system.llm_timeout_seconds` |
| LLM job 返回 `success=False` | `ctx.final_error = result.error`，`_publish_delivery` | — |
| Tool job 超时 | 超时 tool 标记为 `is_error=True, content="timed out"`，继续 loop | `system.tool_wait_seconds` |
| Tool job 返回 `success=False` | `is_error=True` 的 tool_result，LLM 下一轮可见错误 | — |
| Agent loop 超迭代上限 | `ctx.final_reply = "已超过最大工具调用次数..."`，`_publish_delivery` | `system.tool_max_iterations` |
| Cancel | `_process` 立即 return，`ctx.cancelled = True`，`_publish_delivery(fallback)` | — |
| 未知异常 (Exception) | `_run` 的 except 兜底，`ctx.final_error = "agent_crashed:<type>"`，`_publish_delivery(fallback)` | — |

**关键原则**：AgentWorker 自身不 crash。任何异常都兜底为 `ChatJobResult(success=False, error_code=...)` + `_publish_delivery(fallback)`。`_run` 的 `try/except` 必须写 `ctx.final_error`，否则 `submit_result(success=True)` 会让 channel 端误以为成功。回复统一由 `_publish_delivery` 走 `delivery_job_board`，不写入 `ChatJobResult`。

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

AgentWorker        │   chat_job_board    │  AgentWorker 自身
───────────────────┤                     ├─────────────────
release()          │ 退回 pending 给 steering
claim_for_conversation()
                   │  ← 同会话新 ChatJob
submit_result()    │ → ChatJobResult    │ 完成当前 job
```

AgentWorker **不直接调用** `provider.chat()` / `tool.run()` / a2a runtime。所有跨 worker 通信通过 job board 的 publish → claim → submit_result 模式进行。

---

## 8. 实现计划

### Phase 1: 核心 loop（本次）

**文件**：
- `magi/agent/worker.py` — AgentWorker 重写
- `magi/new_bus/guild/chatJob.py` — `claim_for_conversation`
- `magi/new_bus/guild/base.py` — `BaseJobBoard.release()` + `cancel()`

**步骤**：

1. ✅ 构造注入 `NewBus`、`start/stop` 生命周期（已完成）
2. ⬜ `BaseJobBoard.release()` + `BaseJobBoard.cancel()`
3. ⬜ `chatJobBoard.claim_for_conversation(conversation_id)`
4. ⬜ `RunContext` dataclass + `AgentWorker._active_sessions: set[(uid, conv_id)]`
5. ⬜ 实现 `_run()`：claim → cancel 分支 → active_sessions 检查 → release / 启动 _process → submit result（**except 必须写 final_error**）
6. ⬜ 实现 `_process()` 主逻辑 + `RunContext.cancelled` 标志
7. ⬜ 实现 `_gather_all()`（**A2A 独立 task，不与 tool 共享 pending**；steering 上限 16）
8. ⬜ 实现 `_split_tools()`（`message_magi` → a2a_job_board，其余 → tool_job_board）
9. ⬜ 实现 `_build_llm_job()` + `_system_prompt()`（**Phase 1 临时内联**，Phase 2 删）
10. ⬜ 实现 `_publish_delivery()`（destination=None，依赖 DeliveryWorker 反查）
11. ⬜ 实现 `_load_history()`（从 `sessions_book` + `messages_book`）
12. ⬜ 实现 `_tool_schemas()`（从 `tool_definitions_book.list_schemas`）
13. ⬜ 实现 `_append_tool_result_user_message()`
14. ⬜ 实现 `_record_token_usage()`
15. ⬜ 实现 settings 读取 helper（max_iterations / max_tokens / wait seconds / llm timeout）
16. ⬜ 实现 `_maybe_title()`（Phase 1 仅日志；Phase 2 接回）
17. ⬜ 实现 cancel 路径（`_broadcast_cancel` + `RunContext.cancel_event`）
18. ⬜ 删除旧 placeholder DTO（`BusClaim`、`AgentMessage`、`RunResult`、`StreamEvent`、`A2AInvocationRequest`）
19. ⬜ 删除旧 stub 方法（`_complete_agent_input`、`_fail_agent_message`、`_load_tool_continuation`、`_pending_steering_inputs`、`_enqueue_title_if_needed`）
20. ⬜ **测试**：见 §10

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

**额外**：`_system_prompt` 块 4/5 需要给 `ContactBook` 加 `get_by_uid` / `list_notes_for_uid` / `read_daily_note_for_uid`；`SettingsBook` 加 `compaction_policy()` / `show_daily_note()` / `show_daily_note_prompt()`；`MembershipBook` 加 `instruction_context(magic_id)`。这些是 Book 层适配，独立 PR。

### Phase 3: Channel 端

Channel (TG/API) 端 publish `ChatJob` 到 `chat_job_board` 替代旧的 `submit_agent_message`。Channel 端的迁移不在本设计书范围。

---

## 9. 未覆盖项 / 已知限制

以下特性由后续步骤单独处理，**Phase 1 不实现**：

- **Compaction**：Phase 1 跳过；意味着长会话在 tool loop 内会持续累积 messages，最终超出 LLM context window。**Phase 1 验收必须包含"长会话压测"以明确这个限制的触发点**。
- **流式输出**：`bus.stream_hub` 已就绪，但 AgentWorker 暂不消费流式（`streaming=False`）。用户体验上等同于老 bus 的非流式路径。
- **Auto title**：Phase 1 仅日志；新会话首次对话无标题。
- **Multi-conv 同 uid**：当前 `_active_sessions` key = `(uid, conv_id)`。**group chat 等"同一 conv_id 跨多个 channel 来源"的场景需要重新审视 key 形状**——可能应改成 `(uid, conv_id, channel)`。Phase 1 暂用 `(uid, conv_id)`，Phase 2 视业务反馈调整。
- **`DeliveryWorker` 反查 `delivery_address`**：`_publish_delivery` 的 `destination=None` 假设 `DeliveryWorker` 已迁到 new_bus 并实现反查路径。**Phase 1 验收必须确认 DeliveryWorker 已就绪**。
- **`chat_job_board` vs `agent_job_board` 命名一致性**：NewBus 上 `agent_job_board` 字段仍指向 `runAgentJobBoard`，文档选择 `chat_job_board` 作为 agent 的输入队列。**两个 board 的职责划分需要在 NewBus bootstrap 注释中显式声明**，避免后人混淆。
- **A2A worker**：`bus.a2a_job_board` 已存在但 A2A worker 本体是否迁到 new_bus 不在本设计书范围。Phase 1 假设 A2A worker 已就绪；`_gather_all` 的 a2a task 路径会调用其 `get_result`。

---

## 10. 测试策略

Phase 1 至少需要以下测试：

1. **单元：单 ChatJob → 单轮 LLM → 无 tool → 投递**
   - mock `llm_job_board.get_result` 返回固定 CallLLMResult
   - 断言：`_publish_delivery` 被调用一次；`chat_job_board.submit_result(success=True)` 被调用一次

2. **单元：单 ChatJob → LLM 返回 tool_use → tool 完成 → 第二轮 LLM → 投递**
   - mock tool_job_board publish + get_result
   - 断言：第二轮 `_build_llm_job` 的 messages 包含 tool_result user message

3. **单元：steering 注入**
   - 第一轮 LLM 返回 tool_use，进入 `_gather_all`
   - 同时 publish 一条同 `conversation_id` 的 ChatJob
   - 断言：第二轮 messages 末尾追加了 steering text；steering ChatJob 被 `submit_result(success=True)`

4. **单元：cancel 路径**
   - `_run` claim 到 `kind=run.cancel`
   - 断言：`submit_result(success=True)`；`broadcast_cancel` 被调用；`_process` 不进入

5. **集成：内存 SQLite 真 board**
   - 用 `bootstrap_new_bus(state_dir=tmp_path)` 真起 board
   - publish 真实 ChatJob，跑 `_process`，断言 ORM 行状态正确

6. **崩溃恢复**：模拟 worker crash（cancel task 不调 finally），重启后 lease 过期，新 worker claim 到旧 job

7. **性能 / 压力**：长会话（500 messages）不 OOM；100 条 steering 连发不爆 prompt（验证 `MAX_STEERING_PARTS`）；并发的同 conv 第二条正确进 steering 而不是新 run

---

## 附录 A: 新增 / 修改的方法清单

| 模块 | 改动 |
|---|---|
| `magi/new_bus/guild/base.py` | 新增 `BaseJobBoard.release(key, decrement_attempts=True)`；新增 `BaseJobBoard.cancel(key)` |
| `magi/new_bus/guild/chatJob.py` | 新增 `chatJobBoard.claim_for_conversation(conversation_id)` |
| `magi/new_bus/bootstrap.py` | `NewBus` 字段注释中明确 `agent_job_board` vs `chat_job_board` 职责 |
| `magi/agent/worker.py` | 重写 AgentWorker（按本设计 §3、§4） |

---

## 附录 B: 与旧 bus 的字段映射

| 旧 bus 用法 | new_bus 对应 |
|---|---|
| `BusStore.publish_agent_message(AgentMessage)` | `chat_job_board.publish(ChatJob)` |
| `BusStore.claim_next_agent_message(worker_id)` | `chat_job_board.claim()` |
| `BusStore.commit_agent_transition(event_id, ...)` | `chat_job_board.submit_result(key, ChatJobResult(...))` |
| `BusStore.fail_agent_message(event_id, ...)` | `chat_job_board.submit_result(key, ChatJobResult(success=False, error_code=...))` |
| `BusStore.complete_agent_input(event_id)` | 同上（submit_result 通用） |
| `BusStore.load_tool_continuation(run_id)` | 删除（设计不再需要：tool 完成事件由 worker 自身在 `_gather_all` 中直接读到，不走 store） |
| `BusStore.pending_steering_inputs(run_id)` | `chat_job_board.claim_for_conversation(conversation_id)` |
| `BusStore.recover_expired_leases()` | `BaseJobBoard._claim` 自动处理 |
| `BusStore.expire_a2a_invocations()` | `BaseJobBoard._claim` 自动处理 |
| `BusStore.is_run_within_deadline(run_id)` | Phase 1 不实现 deadline；后续在 `ChatJob.metadata` 加 `deadline_at` |
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