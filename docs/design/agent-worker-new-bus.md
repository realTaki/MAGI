# AgentWorker 迁移到 new_bus 设计书

## 状态

**当前阶段**：骨架就位，stub 待填。

`magi/agent/worker.py` 已完成第一轮重构（构造注入 `NewBus`、去掉 `magi.bus` 依赖），所有旧 `magi.bus` store 调用已替换为 `raise NotImplementedError(...)` 占位 stub。`magi/startup/runtime.py` 已改为 `start_agent_worker(bus=new_bus)`。其余 agent 子模块（`agent_context.py`、`system_prompt.py`、`compaction.py`、`auto_title.py` 等）仍走老 `magi.bus`，按"以后再修"策略暂不动。

本设计书覆盖 `worker.py` 的下一步实现。

---

## 1. 架构总览

```
                          ┌──────────────┐
   Channel (TG/API) ────→│ agent_job_board│  (ChatJob)
                          │   (=agent_inbox)│
                          └──────┬───────┘
                                 │ claim / claim_for_conversation
                          ┌──────▼───────┐
                          │  AgentWorker  │
                          │               │
                          │   _process()  │
                          │     │         │
                          │     ├─ context assembly
                          │     │   (sessions_book,
                          │     │    memory_book,
                          │     │    prompt_book,
                          │     │    skills_book,
                          │     │    tool_definitions_book)
                          │     │         │
                          │     ├─ llm_job_board.publish(CallLLMJob)
                          │     │   → wait_for_result()
                          │     │         │
                          │     ├─ [no tool] → delivery_job_board
                          │     │         │
                          │     ├─ [has tool] → tool_job_board × N
                          │     │   → gather wait (concurrent poll
                          │     │     tool results + claim steering)
                          │     │   → loop back
                          │     │         │
                          │     └─ [message_magi] → a2a_job_board
                          │               │
                          └──────┬───────┘
                                 │ submit_result
                          ┌──────▼───────┐
                          │ agent_job_board│  (ChatJobResult)
                          └──────────────┘
```

**AgentWorker 只依赖 `NewBus`**。所有外部操作（LLM 调用、工具执行、消息投递）通过对应的 job board 进行。AgentWorker 自身作为协调者，负责：

1. 从 `agent_job_board` 认领 `ChatJob`
2. 驱动 agent loop（上下文组装 → LLM 推理 → 结果分发）
3. 提交 `ChatJobResult` 完成本轮

---

## 2. 核心数据结构

### 2.1 RunContext

单次 `ChatJob` 引发的完整 agent run 的全部内存状态。一次 `_process()` 调用对应一个 `RunContext` 实例。

```python
@dataclass
class RunContext:
    """Single ChatJob → agent run. All mutable state lives here."""

    # identity (from ChatJob.payload)
    uid: int | None
    session_id: str | None
    channel: str
    caller_role: str | None

    # conversation_id = ChatJob.conversation_id，用于 steering 过滤
    conversation_id: str

    # 累积消息历史（用于组装下一轮 CallLLMJob.messages）
    messages: list[dict]

    # agent loop 迭代上限
    max_iterations: int

    # 结果
    final_reply: str = ""
    final_error: str | None = None
```

**lifecycle**：由 `AgentWorker._run()` 在 claim 到 `ChatJob` 后构造，`_process()` 结束后销毁。

**与上版对比**：不再持有 `steer_queue` / `steer_event`——steering 通过 `agent_job_board.claim_for_conversation(conversation_id)` 直接从 board 认领，board 自身是唯一的状态协调点。

### 2.2 AgentWorker

```python
class AgentWorker:
    bus: NewBus                       # 构造注入
    poll_seconds: float = 0.25
    max_iterations: int = 10          # agent loop 最大迭代次数

    _task: asyncio.Task | None        # 主循环 task
    _stopping: bool                   # 退出信号
    _active_sessions: set[str]        # conversation_id 集合（防重复 _run 启动）
```

**与上版对比**：`_active: dict[str, RunContext]` 简化为 `_active_sessions: set[str]`，不再需要注入到 `RunContext` 的内部队列。

### 2.3 chatJobBoard 新增方法

```python
# magi/new_bus/guild/chatJob.py

class chatJobBoard(BaseJobBoard[_AgentInboxRow, ChatJob, ChatJobResult]):
    # ... existing ...

    def claim_for_conversation(self, *, conversation_id: str) -> ChatJob | None:
        """Claim a ChatJob scoped to one conversation.

        Used by AgentWorker to pull steering messages for an active
        agent run.  Behaves identically to ``claim()`` but filters
        ``conversation_id``, and never falls through to picking
        the oldest global pending row.
        """
        with self._session() as s:
            now = utcnow_naive()
            lease_until = now + timedelta(seconds=self._lease_seconds)
            row = s.scalar(
                select(_AgentInboxRow)
                .where(
                    _AgentInboxRow.conversation_id == conversation_id,
                    or_(
                        _AgentInboxRow.status == "pending",
                        and_(
                            _AgentInboxRow.status == "processing",
                            _AgentInboxRow.leased_until < now,
                        ),
                    ),
                )
                .order_by(_AgentInboxRow.created_at, _AgentInboxRow.id)
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

**关键**：`claim_for_conversation` 只在 `conversation_id` 匹配的行中查找，不会认领其他会话的消息。

---

## 3. 主流程

### 3.1 `_run()` — 主循环

```python
async def _run(self):
    while not self._stopping:
        job = await asyncio.to_thread(self.bus.agent_job_board.claim)
        if job is None:
            await asyncio.sleep(self.poll_seconds)
            continue

        conv_id = job.conversation_id

        # 同 session 已有活跃 run → 退回 pending，
        # 由 _process() 通过 claim_for_conversation 认领
        if conv_id and conv_id in self._active_sessions:
            self._release_claim(job)  # set status='pending', leased_by=None
            continue

        if conv_id:
            self._active_sessions.add(conv_id)

        ctx = RunContext(
            uid=job.payload.get("uid") if job.payload else None,
            session_id=job.payload.get("session_id") if job.payload else None,
            channel=job.payload.get("channel", "") if job.payload else "",
            caller_role=job.payload.get("caller_role") if job.payload else None,
            conversation_id=conv_id or "",
            messages=[],
            max_iterations=self.max_iterations,
        )
        try:
            await self._process(ctx)
        except Exception:
            logger.exception("agent run failed conv=%s", conv_id)
        finally:
            if conv_id:
                self._active_sessions.discard(conv_id)
            self.bus.agent_job_board.submit_result(
                key=job.event_id,
                result=ChatJobResult(
                    event_id=job.event_id,
                    success=ctx.final_error is None,
                    error_code=ctx.final_error,
                    result={"reply": ctx.final_reply} if ctx.final_reply else None,
                ),
            )
```

**与上版的关键区别**：`_run()` 不再注入到 `RunContext` 内部队列。同 session 的 job 被退回 pending，由 `_process()` 自己通过 `claim_for_conversation` 认领——"谁消费谁负责 claim"。

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
     │  7. await _gather_all(     │  ← 内部并发：轮询 tool results
     │       tool_results +       │    + claim_for_conversation
     │       a2a_results,         │    收集 steering 文本
     │       ctx)                 │
     │                            │
     │  8. _append_tool_result_   │
     │     user_message(          │
     │       tool_results,        │
     │       steering_text)       │  ← steering 接在 tool_results 之后
     │                            │
     │  → loop back               │
     └────────────────────────────┘
                   │ (max_iter exceeded)
     _publish_delivery()
     ctx.final_reply = "已超过最大工具调用次数..."
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
        max_tokens=1024,
        tools=tools,
        parameters={
            "uid": ctx.uid,
            "session_id": ctx.session_id,
            "channel": ctx.channel,
            "caller_role": ctx.caller_role,
        },
    )
```

**compaction**（后续补齐）：在 `_build_llm_job` 调用前，先通过 `maybe_compact()` 检查 `ctx.messages` 的 token 估计值，若超过阈值则走 `llm_job_board` 生成摘要并原地裁剪 `ctx.messages`。当前子模块 `compaction.py` 仍走老 `magi.bus`，本步骤暂跳过。

### 4.2 System Prompt 组装 `_system_prompt`

六块顺序拼接：

| # | 块 | 数据源 |
|---|---|---|
| 1 | SOUL (persona) | `prompt_book.get("soul")` 或 workspace `SOUL.md` |
| 2 | Instructions | `memberships_book` + MAGIS role instructions |
| 3 | Long-term memory | `memory_book.list_by_owner(uid)` |
| 4 | Current chatter | `contacts_book.get(uid)` + `contact_notes_book.list_for_contact(uid)` |
| 5 | Daily note | `contact_notes_book` 中 `kind=daily` + `settings_book` toggle 检查 |
| 6 | Skills | `skills_book.list()` 的 meta（name + description） |

**当前策略**：`_system_prompt` 直接内联实现上述逻辑，不调用 `system_prompt.py`（该子模块仍走老 `magi.bus`）。后续等子模块单独迁移后再切回委托调用。

### 4.3 Tool Schemas

```python
def _tool_schemas(self, caller_role: str | None) -> list[dict]:
    defs = self.bus.tool_definitions_book.list_enabled(caller_role=caller_role)
    return [
        {"name": d.name, "description": d.description, "input_schema": d.input_schema}
        for d in defs
    ]
```

### 4.4 等待全部工具结果 + steering 收集 `_gather_all`

**核心设计**：在等待 tool 结果的同时，通过 `claim_for_conversation` 主动从 board 认领同 session 的 steering ChatJob。steering 以 board claim 方式获取，不做进程内队列传递。

```python
async def _gather_all(
    self,
    ctx: RunContext,
    tool_entries: list[tuple[str, str]],  # [(tool_call_id, job_id), ...]
    a2a_results_task: asyncio.Task | None,
) -> tuple[dict[str, RunToolResult], str | None]:
    """
    并发等待全部 tool + a2a 完成，同时收集同 session 的 steering 文本。

    Returns:
        tool_results: tool_call_id → RunToolResult
        steering_text: 收集到的 steering 文本拼接，无 steering 时为 None
    """
    pending: dict[str, str] = {jid: tid for tid, jid in tool_entries}
    results: dict[str, RunToolResult] = {}
    steering_parts: list[str] = []
    deadline = asyncio.get_running_loop().time() + 300.0

    while pending:
        # ── 尝试 claim 同 session 的 steering ChatJob ──
        if ctx.conversation_id:
            steer = await asyncio.to_thread(
                self.bus.agent_job_board.claim_for_conversation,
                conversation_id=ctx.conversation_id,
            )
            if steer is not None:
                text = (steer.payload or {}).get("text") or ""
                if text:
                    steering_parts.append(text)
                # 立即完成 job，不做独立 run
                self.bus.agent_job_board.submit_result(
                    key=steer.event_id,
                    result=ChatJobResult(
                        event_id=steer.event_id,
                        success=True,
                        status="consumed_by_steering",
                    ),
                )

        # ── 检查 tool 结果 ──
        for job_id, tool_id in list(pending.items()):
            result = await asyncio.to_thread(
                self.bus.tool_job_board.get_result, key=job_id,
            )
            if result is not None:
                results[tool_id] = result
                del pending[job_id]

        if not pending:
            break

        # ── A2A 检查（非阻塞）──
        if a2a_results_task is not None and a2a_results_task.done():
            a2a_results = a2a_results_task.result()
            for tc_id, ar in a2a_results.items():
                results[tc_id] = _a2a_to_tool_result(ar)
                # 从 pending 移除对应的 tool_call_id（如果有）
                for jid, tid in list(pending.items()):
                    if tid == tc_id:
                        del pending[jid]

        if not pending:
            break

        if asyncio.get_running_loop().time() >= deadline:
            logger.warning("tool wait timeout: %d jobs still pending", len(pending))
            for job_id, tool_id in pending.items():
                results[tool_id] = RunToolResult(
                    job_id=job_id,
                    success=False,
                    content="tool execution timed out",
                    is_error=True,
                    tool_call_id=tool_id,
                )
            break

        await asyncio.sleep(0.1)

    steering_text = "\n\n".join(steering_parts) if steering_parts else None
    return results, steering_text
```

**关键点**：

1. 每次轮询 cycle 都尝试 `claim_for_conversation`——如果同 session 有新 ChatJob pending，会被认领并立即 submit 为 `consumed_by_steering`
2. steering ChatJob 不触发独立 run，文本被累积到 `steering_parts` 中
3. tool 结果和 steering 收集在同一轮询循环中，自然并发
4. A2A 通过 `a2a_results_task` 并行等待，完成后结果合并到 `results`

### 4.5 Tool Result User Message 组装

```python
def _append_tool_result_user_message(
    ctx: RunContext,
    tool_results: dict[str, RunToolResult],  # tool_call_id → result
    a2a_results: dict[str, SendA2AResult] | None,
    steering_text: str | None,
) -> None:
    """追加一条 user 消息：tool_result blocks 在前，steering 文本接在最后。

    LLM API 约束：assistant(tool_use) 必须紧跟 user(tool_result)。
    tool_result blocks 在先，steering 以 ``{"type":"text"}`` block 接在末尾。
    """
    blocks: list[dict] = []
    for tool_call_id, tr in tool_results.items():
        blocks.append({
            "tool_use_id": tool_call_id,
            "type": "tool_result",
            "content": tr.content,
            "is_error": tr.is_error,
        })
    if a2a_results:
        for tc_id, ar in a2a_results.items():
            blocks.append({
                "tool_use_id": tc_id,
                "type": "tool_result",
                "content": ar.response.get("text", "") if ar.response else "",
                "is_error": not ar.success,
            })
    if steering_text:
        blocks.append({"type": "text", "text": steering_text})

    ctx.messages.append({
        "role": "user",
        "content": steering_text or "",
        "content_blocks": blocks,
    })
```

### 4.6 `_run()` 退回误认领的 job

```python
def _release_claim(self, job: ChatJob) -> None:
    """Release a claim made by _run() so _process() can reclaim it as steering.

    Resets the row to 'pending' and clears the lease.  The next
    ``claim_for_conversation`` call inside ``_process()`` will pick it up.
    """
    with self.bus.agent_job_board._session() as s:
        row = s.scalar(
            select(_AgentInboxRow).where(
                _AgentInboxRow.event_id == job.event_id,
            )
        )
        if row is not None:
            row.status = "pending"
            row.leased_by = None
            row.leased_until = None
            row.attempts = max(0, row.attempts - 1)  # 解租约不消耗重试次数
            s.commit()
```

### 4.7 投递 `_publish_delivery`

```python
def _publish_delivery(self, ctx: RunContext) -> None:
    self.bus.delivery_job_board.publish(DeliveryJob(
        channel=ctx.channel,
        payload={
            "text": ctx.final_reply,
            "session_id": ctx.session_id,
            "uid": ctx.uid,
        },
        destination=None,  # 由 DeliveryWorker 按 channel 解析
    ))
```

### 4.8 标题生成 `_maybe_title`

```python
def _maybe_title(self, ctx: RunContext) -> None:
    """在第一个 assistant turn 后异步生成会话标题。

    后续补齐：当前 auto_title.py 走老 bus，此处用 llm_job_board 直接实现。
    """
    # 1. sessions_book.get_for_owner(uid, session_id)
    #    → 检查 title is None 且 messages count == 2
    # 2. llm_job_board.publish(CallLLMJob(
    #        kind="auto_title", system=title_prompt,
    #        messages=[("user", first_user_text)]))
    # 3. asyncio.create_task(poll + sessions_book.set_title_if_null)
    pass  # 后续补齐
```

### 4.9 Token Usage 记录

后续补齐：`token_usage_book` 在 `_process` 中 LLM 调用成功后调用。

---

## 5. Steering 设计

### 5.1 设计原则：board 即协调点

不需要进程内队列（`asyncio.Queue`）或 Event，`agent_job_board` 本身是持久化的状态协调点。AgentWorker 通过在 `_gather_all` 中主动 `claim_for_conversation` 来认领同 session 的新消息。

### 5.2 数据流

```
Channel publishes ChatJob(conv_id="abc", text="再查一下")
        │
        ▼
AgentWorker._run()  calls claim()
        │
        ├─ "abc" in _active_sessions?  YES
        │
        ├─ _release_claim(job)   ← 解租约，退回 pending
        └─ continue              ← 不启动新 run
        │
        │  (与此同时，_process("abc") 在 _gather_all 中运行)
        │
        ▼
_gather_all() calls claim_for_conversation(conversation_id="abc")
        │
        ├─ 认领到 ChatJob(text="再查一下")
        ├─ steering_parts.append("再查一下")
        └─ submit_result(consumed_by_steering)
        │
        ▼
tool 完成后 → _append_tool_result_user_message(steering_text="再查一下")
            → 文本接在 tool_result blocks 之后
            → 下一轮 LLM 调用看到 steering
```

### 5.3 注入时机

**steering 只在 tool_result user 消息末尾注入**。原因：

1. LLM API 约束：`assistant(tool_use)` → `user(tool_result)` 必须严格配对，中间不能插入 steering 文本
2. `tool_result` 后的 user 消息末尾可以安全地追加 `{"type": "text", "text": steering}` content block
3. 如果 LLM 返回无 tool_use（terminal），不会进入 `_gather_all`，也就不存在 tool_result 注入点。此时 `_active_sessions` 中的记录会在 `_process` 结束后清除，用户的下一条消息会作为正常 ChatJob 在下一轮 `_run()` 中被 claim

### 5.4 多段 steering 的处理

`_gather_all` 的轮询循环中每次都尝试 `claim_for_conversation`。如果用户在 tool 执行期间连续发了多条消息，每一条都会被陆续认领并追加到 `steering_parts`。最终拼接为一段文本（`"\n\n"` 分隔）。

### 5.5 与旧设计对比

| | 旧设计 (steer_queue + Event) | 新设计 (board claim) |
|---|---|---|
| RunContext 额外字段 | 2 (queue, event) | 0 |
| 协调机制 | 进程内 in-memory | SQLite board（天然持久化） |
| `_run()` 路由 | 注入到 context.queue | 解租约退回 pending |
| `_process()` 感知 | 被动 Event.set() 通知 | 主动 poll claim_for_conversation |
| 代码量 | 多一个队列消费逻辑 | claim_for_conversation 复用现有 claim 模式 |
| 语义 | "注入" | "认领"——符合 board 哲学 |
| 崩溃恢复 | 队列丢失 | board 中 steering job 仍在 pending，下一个 run 继续认领 |

---

## 6. 异常处理

| 场景 | 处理 |
|---|---|
| LLM job 超时 (120s) | `ctx.final_error = "LLM call timed out"`，`_publish_delivery` |
| LLM job 返回 `success=False` | `ctx.final_error = result.error`，`_publish_delivery` |
| Tool job 超时 (300s) | 超时 tool 标记为 `is_error=True, content="timed out"`，继续 loop |
| Tool job 返回 `success=False` | `is_error=True` 的 tool_result，LLM 下一轮可见错误 |
| Agent loop 超迭代上限 | `ctx.final_reply = "已超过最大工具调用次数"`，`_publish_delivery` |
| 未知异常 (Exception) | `logger.exception`，`_publish_delivery(fallback)` |

**关键原则**：AgentWorker 自身不 crash。任何异常都兜底为 `ChatJobResult(success=False)` + `_publish_delivery(fallback)`。

---

## 7. 与 providers/tools worker 的关系

```
AgentWorker  │    LLM job board    │  ProvidersWorker
─────────────┤                     ├─────────────────
 publish     │ ──── CallLLMJob ──→ │ claim → execute
             │                     │       → submit_result
 wait_for_   │ ←─ CallLLMResult ── │
 result()    │                     │

AgentWorker  │    Tool job board   │  ToolsWorker
─────────────┤                     ├─────────────────
 publish × N │ ──── RunToolJob ──→ │ claim → execute
             │                     │       → submit_result
 _gather_    │ ←─ RunToolResult ── │
 all()       │                     │
```

AgentWorker **不直接调用** `provider.chat()` 或 `tool.run()`。所有跨 worker 通信通过 job board 的 publish → claim → submit_result 模式进行。

---

## 8. 实现计划

### Phase 1: 核心 loop（本次）

**文件**：`magi/agent/worker.py` + `magi/new_bus/guild/chatJob.py`

1. ✅ 构造注入 `NewBus`、`start/stop` 生命周期（已完成）
2. ⬜ `chatJob.py` 新增 `claim_for_conversation(conversation_id)` 方法 + `from magi.new_bus.guild.base import MAX_ATTEMPTS`
3. ⬜ 实现 `_run()`：claim → active_sessions 检查 → _release_claim / 启动 _process → submit result
4. ⬜ 实现 `RunContext`（简化版，无 queue/event）+ `_process()` 主逻辑
5. ⬜ 实现 `_gather_all()`（并发 poll tool results + claim_for_conversation steering）
6. ⬜ 实现 `_release_claim()`
7. ⬜ 实现 `_build_llm_job()` + `_system_prompt()`（内联实现）
8. ⬜ 实现 `_publish_delivery()`
9. ⬜ 实现 `_load_history()`（从 `sessions_book` + `messages_book`）
10. ⬜ 实现 `_tool_schemas()`（从 `tool_definitions_book`）
11. ⬜ 实现 `_append_tool_result_user_message()`
12. ⬜ 删除旧 placeholder DTO（`BusClaim`、`AgentMessage`、`RunResult`、`StreamEvent`、`A2AInvocationRequest`）
13. ⬜ 删除旧 stub 方法（`_complete_agent_input`、`_fail_agent_message`、`_load_tool_continuation`、`_pending_steering_inputs`、`_enqueue_title_if_needed`、`_consume_steering_text`）

### Phase 2: 子模块迁移（后续）

每个子模块独立迁移到 `NewBus` 构造注入：

| 文件 | 改动 |
|---|---|
| `agent_context.py` | `get_bus().session` → `bus.sessions_book`；`get_bus().tool_catalog` → `bus.tool_definitions_book` |
| `system_prompt.py` | `get_bus().memory/contacts/settings/magic` → 对应 new_bus Books |
| `compaction.py` | `get_bus().settings/session` → new_bus Books；`enqueue_llm_job` → `llm_job_board` |
| `auto_title.py` | `get_bus().session` + `enqueue_llm_job` → new_bus |
| `instructions.py` | `get_bus().magic` → MAGIS Books |
| `token_usage.py` | `get_bus().token_usage` → `token_usage_book` |

### Phase 3: Channel 端（后续）

Channel (TG/API) 端 publish `ChatJob` 到 `agent_job_board` 替代旧的 `submit_agent_message`。

---

## 9. 未覆盖项

以下特性由子模块迁移时单独设计，不在 Phase 1 范围：

- **Compaction**：仍在 `compaction.py` 中走老 bus，Phase 1 跳过
- **Token 粗估**：`tokens.py` 是纯计算，无 bus 依赖，随时可用
- **流式**：`bus.stream_hub` 已就绪，但 AgentWorker 暂不消费流式输出（streaming=False）
- **A2A**：`bus.a2a_job_board` 已存在，Phase 1 中 A2A 工具调用走 `_gather_all` 的统一等待路径；实际 A2A 投递由 `sendA2AJobBoard` + worker 处理
- **chat_job_board 改名**：NewBus 上字段已统一为 `agent_job_board`，但 board 类是 `chatJobBoard`，当前不做额外重命名
