---
title: 关键业务流程
description: MAGI 核心业务逻辑的行为不变式和关键守卫条件。
lang: zh-CN
permalink: /business-flows/
---

# MAGI 关键业务流程

> 本文档记录核心业务逻辑的**行为不变式**和关键守卫条件。
> 改动这些模块时必须保持以下行为不变，否则会导致生产问题。
>
> **当前 cutover**：所有运行时路径都走 `magi.bus` Job Board 模型
> （`agent_job_board` / `delivery_job_board` / `tool_job_board` /
> `llm_job_board` / `a2a_request_job_board` / `a2a_notify_job_board` /
> `change_mcp_server_job_board` /
> `seed_preset_tasks_job_board` / `change_provider_config_job_board` /
> `run_task_job_board`）。旧的 `magi.bus.BusStore` / `agent_turn_store`
> / `magi.agent.step.run_agent_step` / `magic` 表 等已删除；文档里
> 残留的旧符号仅作**行为锚点**参考，不是当前可调用路径。
>
> **A2A（同一 MAGIS 内 MAGI ↔ MAGI 协作）**：是 MAGIS 共享数据库上的
> **持久 actor effect**，不是 channel，也不是 HTTP/webhook/外部签名
> 协议。消息以 `magis_memberships.id` 作为 `magi_id` 写入
> `a2a_request_job_board`（一次 request / 一次 response）和
> `a2a_notify_job_board`（单向 fire-and-forget），
> `AgentWorker.claim_next_turn()` 公平地与本地 `agent_job_board` 一并消费。
> 旧的 `sendA2AJob` / `A2AWorker` /
> `channels/a2a/{adapter,router,transport,protocol}.py` / 任何 `expect_reply`
> / `reply_to` / HTTP 地址参数均已删除；`message_magi` 工具 schema 收敛为
> `{magi_id, mode ∈ {notify, request}, text, deadline_seconds}`。
>
> **Provider 凭证**：来自 `bus.settings_book` 三个 key —
> `provider.name` / `provider.api_key` / `provider.model`。写侧唯一入口
> 是 `bus.change_provider_config_job_board.publish(ChangeProviderConfigJob(...))`
> （`publish()` 自包含：先落 settings_book 再建 job 行）。
>
> **Cookie**：v4 selected-MAGI session（`v4.<base64(payload)>.<sig>`，
> payload 含 `v=4, magi_id, tgid, display_name, admin, assigned,
> ts`）。v3 旧 cookie（payload 用 `telegram_id`）在升级时强制失效。
>
> **实际入口**：
>
> - Agent Loop → `magi/agent/worker.py::AgentWorker._run` → `_process`
> - Channel egress → `magi/channels/worker_base.py::_claim_delivery_loop`（每个 channel worker 的 `_run` 拉起自己的循环）
> - Channel ingress → `magi/channels/telegram/worker.py::_on_tg_message` 等
> - Credential 解析 → `magi/providers/factory.py::get_provider(bus=...)`
> - Task 调度 → `magi/channels/tasks/worker.py::TaskWorker._run`
> - 手动 / tool 触发任务 → `bus.run_task_job_board.publish(RunTaskJob(...))`（走 `magi/bus/firmwares/jobs/runTaskJob.py`）
> - 系统级主动策略 → `magi/proactive/worker.py::ProactiveWorker._run`
> - 外部数据流 → `magi/connectors/`（按需启动，非默认 Worker）
>
> **命名约定**：ID 命名以 [Terms]({{ '/terms/#canonical-id-names' | relative_url }}) 为准 ——
> `magi_id` / `contact_id` / `conversation_id` / `job_id` / `tgid`。本文件里
> 出现的历史名（`magic_id` / `uid` / `session_id` / `event_id` /
> `telegram_id`）只在描述已失效的 cookie、已删除的 meta key、已改名的列时
> 出现，标注为历史。

---

## 1. Agent Loop — 消息处理主循环

**入口**: `magi.agent.worker.AgentWorker` → `AgentWorker._run` → `_process`

```
1. 工具目录同步
   ├─ MCP 工具由 McpWorker 在启动时引导注入到 registry；
   │  运行时通过 change_mcp_server_job_board 异步处理变更
   │  （add/update/delete → 重连 → register_tools("mcp", ...)）
   └─ ToolsWorker.on_tools_changed 自动检测 → 重发布 catalog 到
      tool_definitions_book

2. LLM 凭据解析 (magi/providers/factory.py::get_provider)
   └─ 严格模式：从 bus.settings_book 读 provider.name /
     provider.api_key / provider.model（这是 settings_book.KNOWN_KEYS
     里的 per-MAGI 字段，历史上从 magic 表迁移而来）
   └─ Contact 表不含 provider/api_key（已被移除）
   └─ 配置缺失 → LLMNotConfiguredError → 503 m
     `api.llm_credentials_required`
   └─ 未知 provider → LLMError
   └─ **绝**不接 provider/api_key 作为参数；调用方（_build_llm_job /
     compaction / auto_title）必须依赖工厂从 settings_book 读取

3. 构建上下文 (AgentWorker._build_llm_job)
   ├─ llm_job = CallLLMJob(system=..., messages=..., tools=...)
   ├─ system = build_system_prompt(contact_id, soul, bus, magi_id=...)：
   │   六块顺序固定 — SOUL → Instructions → Memory → Contact →
   │   Daily note → Skills
   │   ├─ SOUL = read_soul(bus) — bus.prompt_book.soul() 读 workspace
   │   │   prompts/agent/soul.md，否则回退到 AgentWorker 管理的默认 persona
   │   ├─ Instructions = runtime_instruction_block(bus, magi_id=...)
   │   │   — 含 personal instruction + team/role 层（从 MAGIS
   │   │   memberships_book 读）
   │   ├─ Memory = bus.memory_book.list_by_owner(contact_id)
   │   ├─ Contact = bus.contacts_book.get + contact_notes_book
   │   │   .list_for_contact + read_daily_note
   │   └─ Skills = bus.skills_book.list()（file-backed，workspace 管理目录）
   └─ tools = bus.tool_definitions_book.list_enabled(caller_role=contact.role)

4. 工具循环 (AgentWorker._process 的 for _ in range(max_iterations))
   for _ in range(max_iterations):
   ├─ [每轮] cancel check (ctx.cancel_event.is_set())
   ├─ [每轮] llm_job_board.publish(CallLLMJob) → wait_for_result
   │   （默认 120s；失败 → ctx.failed=True +
   │    final_reply = _format_llm_error(result) — 透传
   │    ``error_code: error`` 给用户，agent 不 paraphrase，
   │    publish delivery → return）
   ├─ [每轮] record_token_usage（按 contact_id 入账 token_usage_book）
   ├─ [每轮] _split_tools(ctx, tool_uses) → tool_jobs / a2a_jobs
   ├─ [每轮] _publish_effects(split) → 收集 tool_call_id → job_id
   ├─ [每轮] _gather_all(ctx, split, tool_ids) — 并发 poll tool /
   │   a2a + claim_for_steering 拾 steering
   ├─ [每轮] _append_tool_result_user_message() — 把 tool_result blocks
   │   + steering 拼成下一轮 user 消息
   ├─ [LLM_REQUEST_PREPARED + LLM_RESPONSE_RECEIVED hook gates]
   └─ 终止: 无 tool_uses / max_iterations exceeded / cancel / LLM failure

5. 终态 (AgentWorker._process 的 commit 收尾)
   ├─ 无错误 / 有 reply → delivery_job_board.publish(DeliveryJob(
   │     channel=ctx.channel, payload={text, conversation_id,
   │     contact_id})) → channel worker claim → 投递
   ├─ 异常 → ctx.failed=True + final_reply 兜底文案 + publish delivery
   └─ cancel → ctx.final_reply = "任务已取消。" + publish delivery
     （**不**制造伪造 assistant reply — 避免污染 transcript）
   └─ ChatNotifyResult(status="completed"/"failed") 写回 agent_job_board
     （channel worker 只看 status，不读 error_code —— 失败文案已
      通过上面的 DeliveryJob 投到频道）

6. 后台 (fire-and-forget)
   └─ _maybe_title → spawn request_session_title（独立 task）
```

**不可改的守卫**:

- `get_provider(bus=..., model=...)` 必须是 strict mode — MAGI 未配 provider/api_key → `LLMNotConfiguredError`，**绝不**回退到任何默认凭证；调用方 **绝不能**接受 provider/api_key 作为参数，必须依赖工厂从 `bus.settings_book` 读取。
- 对话消息 store 读取失败必须吞掉（不崩溃主循环）
- tool result 必须在拼接新消息前安全截断（否则 Anthropic API 拒绝交错 tool 块）— 阈值 8000 字符
- system prompt 六块顺序不可变：SOUL → Instructions → Memory → Contact → Daily note → Skills
- cancel 不发送伪造 assistant reply（避免污染 transcript）
- AgentWorker 是 `chatNotifyBoard` 的唯一消费者；不接 `run_agent_step` / `agent.run` 类的 in-process helper

---

## 2. LLM 凭证解析 (`get_provider`)

**入口**: `magi/providers/factory.py::get_provider(bus=...)`

```
设计原则:
  - LLM 凭证（provider.name / provider.api_key / provider.model）来自
    MAGI 本地的 bus.settings_book（这是 settings_book.KNOWN_KEYS 的
    per-MAGI 字段，历史上从 (已删除的) magic 表迁移而来）
  - 接触面位于 Operator 的本地 SQLite；每个 MAGI 各持一份
  - Contact 表不存 provider/api_key（已被移除）— Token 消耗仍按 contact_id
    入账（token_usage.contact_id）
  - 设置变更通过 change_provider_config_job_board.publish 自包含写
    （自动落 settings_book）；runtime_provider FastAPI 路由也直接写

调用链:
  TG bot:    _on_tg_message → _resolve_contact → ChatNotifyJob →
             AgentWorker → get_provider() (bus.settings_book)
  WebUI:     /api/chat/send → publish ChatNotifyJob → AgentWorker →
             get_provider() (bus.settings_book)
  Runner:    TaskWorker._fire_task → publish ChatNotifyJob → AgentWorker →
             get_provider() (bus.settings_book)
```

**不可改的守卫**:

- 绝不从 Contact 表读 provider/api_key（列已移除）
- Token 消耗仍记给 Contact（`token_usage.contact_id = ctx.contact_id`）
- 凭证不完整 → `LLMNotConfiguredError`，不回退默认值
- 未知 provider id → `LLMError`（含已知厂商列表）
- `get_provider()` 是 `providers.worker.ProvidersWorker` 的唯一凭据来源；其它路径必须走工厂

---

## 3. Conversation 生命周期与 D.22 通道守卫

**入口**: `magi/bus/firmwares/books/local/conversationBook.py::{ConversationBook, MessageBook}`

### 创建对话
```
1. validate contact_id — contact 有效性检查
2. 生成新 conversation_id（Crockford-base32 ULID-like, 26 chars）
3. delivery_address 默认值:
   ├─ TG:  str(telegram_chat_id)
   ├─ WebUI: ""（空字符串）
   └─ task: "<scheduled>"
4. ConversationBook.add(conversation_id, contact_id, channel,
   delivery_address, ...)
```

### 追加消息（`MessageBook.add`）— D.22 通道守卫
```
1. validate conversation_id + contact_id
2. message role 校验 — 仅允许 user / assistant / system / tool
3. 加载 conversation 行 → 不存在或 contact_id 不匹配 → 
   ConversationNotFoundError
4. D.22 通道检查（写入者负责；读取不检查，同一用户可从 WebUI 浏览 TG 历史）:
   if requested_channel is not None AND sess_row.channel AND
   sess_row.channel != requested_channel:
       → ChannelMismatchError (HTTP 403)
   └─ 空 channel (legacy 行) 不触发 — 写入者胜
   └─ channel=None 跳过检查（用于回填工具）
5. 事务内: INSERT chat_messages + UPDATE chat_conversations.updated_at
```

**不可改的守卫**:

- **D.22**: 写入必须检查 channel 匹配，读取不检查（同一用户可从 WebUI 浏览 TG 历史）
- 空/旧 conversation 的 channel 不拒绝写入（兼容 pre-D.22 数据）
- `delivery_address` 列对 domain 代码不透明 — 只有 channel worker 在 `_deliver_*` 里解释其值（TG = tgid 字符串；WebUI = ""；task = "<scheduled>"）
- `conversation_id` 是 Crockford-base32 ULID-like（26 chars）；非此格式 → `ConversationPathError`（400，不重试）

---

## 4. Telegram 入站消息

**入口**: `magi/channels/telegram/worker.py::_on_tg_message`
（TelegramWorker 在 `_run()` 里 `asyncio.gather(_run_inbound, _run_outbound)`，
`_run_inbound` 起 `python-telegram-bot` `Application.start_polling`，并注册
`MessageHandler(filters.ALL, _on_tg_message)`；旧 `bot.py::_on_message` 路径已删）

```
1. 提取 tgid = str(update.effective_chat.id)

2. 身份解析 (`_resolve_contact(bus, tgid)`)
   └─ contacts_book.get_by_telegram(tgid=int(tgid)) — 返回
     `(contact_id, role, admin)` 三元组或 None
   └─ legacy `telegram.user.<tgid>.uid` meta key 已删除（`uid` 是历史名，
     现为 `contact_id`）

3. 角色分发:
   ├─ admin=True OR role=="assigned" → 通过，走 agent loop
   │   └─ admin 和 assigned 共享同一处理器 (admin 可在 TG 上与 MAGI 聊天)
   ├─ role=="guest" → 拒绝，发送 tgid 发现消息
   │   └─ `_send_stranger_reply` 同时软自动创建 Contact(role="guest", admin=False)
   │     如果该 tgid 还没绑定 Contact
   └─ 无绑定（contact is None）→ 也走 `_send_stranger_reply`，同样软创建
       Contact(role="guest", admin=False)，并发送 tgid 发现消息

4. 通过后:
   ├─ `conversation_id = _resolve_tg_session(bus, contact_id=..., tgid=...)` —
     调 `bus.conversations_book.get_or_create_for_tg(contact_id=...,
     delivery_address=tgid)`，一个 TG 对话一个持久 conversation
   ├─ `_append_user_message(bus, conversation_id, text)` — 落 user transcript
     （D.22 守卫在写入时执行）
   ├─ fire-and-forget `_send_read_receipt(update, bus)` — 发一个"已读"表情
   └─ `publish_chat(text=..., channel="tg", contact_id=...,
     conversation_id=...)` — 投递到 agent_job_board
     （caller_role / chat_id / tg_message_id 不再随 job 传递；
     AgentWorker 在 claim 时从 contact.role 实时回查）
```

**Contact.role 枚举 (2024 collapse)**:
- 有效值: `assigned` | `guest`（共 2 个）
- 历史值 `admin` 已被迁移到独立 `admin` 布尔字段（见第 2 节"凭证校验"）
- 历史值 `contact` 已被合并入 `guest` — 历史上两个 role 在所有门控路径上行为完全相同（都被拒绝），所以合并是无损的

**不可改的守卫**:

- `guest` 角色必须被拒绝（不属于此 MAGI 服务范围，等待管理员提升）
- `guest` 软自动创建时 admin 必须为 False
- admin 必须能和 assigned 一样聊天（不能退化为 v0 的 no-op）
- 对话持久化（`messages_book.add`）必须在发布 `ChatNotifyJob` 到 `agent_job_board` 之前完成
- `job_id` 形如 `telegram:<tgid>:<message_id>`，提供去重幂等性

---

## 5. Channel 出站消息路由

**入口**: `magi/channels/worker_base.py::_claim_delivery_loop`
（dispatcher.py 已删除；每个 channel worker 各自的 `_run` 拉起自己的 claim loop）

### 出站消息流（每个 channel worker 都遵循）
```
ChannelWorker._claim_delivery_loop(deliver_fn, channel_label):
  1. backpressure check（depth > settings["channels.delivery.max_queue_depth"]
     默认 1000；超过 → 每 channel 每分钟 1 次 warning + 5× poll_seconds 休眠）
  2. delivery_job_board.claim_for_channel(channel_label, worker_id) —
     仅认领本 channel 的 FIFO job，并将 worker_id 写入租约
  3. 其他 worker 不能提交或释放该租约；lease 过期后 job 可由另一 worker 认领
  4. deliver_fn(job) — 实际投递
     （TG → 原始 HTTP send_text_raw；
      WebUI → 写 messages_book；
      A2A → send_a2a_delivery；
      Task → _fire_task → publish ChatNotifyJob，delivery 由下游 channel 处理）
  5. delivery_job_board.submit_result(DeliveryResult(success, error))
  6. 异常 → submit_result(success=False, error=str(exc)[:1024])
     （BUS 不负责重试预算或自动失败；lease 过期后 job 保持在 board，
     等待另一 worker 处理或由 worker 显式提交 FAILED）
```

### TG 出站实际投递 (`magi/channels/telegram/worker.py::_deliver_tg`)
```
TelegramWorker._deliver_tg(job: DeliveryJob):
  ├─ bus.settings_book.get("telegram.bot_token")
  ├─ chat_id = int(job.destination)
  ├─ text = job.payload["text"]
  └─ channels.telegram.bot.send_text_raw(token, chat_id, text)
      └─ 走原始 HTTP（非 bot.send_message）
      └─ 原因: bot 实例绑定 daemon 线程的 event loop，
         从非-daemon loop 调用会静默丢弃
```

**不可改的守卫**:

- WebUI 路径**不走 adapter**（`webui` worker 直接写 `messages_book`，用户 inline 看到）
- TG `TelegramWorker._deliver_tg` **必须走原始 HTTP**（`send_text_raw`），不能用 `bot.send_message`
- Channel worker 在 composition root 启动时一次性 `start()`（不再是 dispatcher 自注册 + 懒加载）
- Domain 代码（tools/runner/webui api）绝不直接读 `delivery_address` 或调 channel worker
- **A2A 不是 channel**：没有 `delivery_job_board` 行、没有 channel worker、没有 `channels/a2a/` 包。所有 A2A 出站都走 `magi/tools/comms/message_magi.py`（持久 actor effect），由 `AgentWorker` 直接落盘到 MAGIS 共享 boards。

---

## 6. 定时任务 — 创建与执行

### 创建 (`schedule_task` 工具 / WebUI API)
```
1. 角色门: admin 或 assigned → 可创建
2. 创建 conversation(channel="task", delivery_address="<scheduled>")
3. INSERT task 行，关联 conversation_id；source = SOURCE_USER
   （preset 行由 ProactiveWorker 插入，source = SOURCE_PROACTIVE）
4. cron 字段由 croniter 校验（取代 apscheduler）；run_at 由
   validate_run_at 规范化到 UTC trailing-Z ISO
5. schedule 互斥：cron XOR run_at，never both / never neither
```

### 执行 (`magi/channels/tasks/worker.py::TaskWorker._run`)
```
1. _rehydrate() — 启动时从 tasks_book 读所有 enabled task 状态，
   用 last_run_at 填充 _next_fire 缓存
2. _reap_stale_runs() — 调 task_runs_book.reap_stale(older_than_seconds=300)
   把超时未收尾的 running 行翻成 failed（"abandoned by previous worker"）
3. 轮询 (poll_seconds 默认 15s):
   ├─ run_task_job_board.claim() — 手动 / API / tool 触发
   │  └─ _handle_run_task_job(rj) → tasks_book.get(task_id=rj.task_id)
   │     → _fire_task(task, manual=rj.manual)
   │     （conversation_id / contact_id 从 Task 行读，不在 rj 上传）
   └─ tasks_book.list_all_enabled_for_workers() — cron / run_at tick
      ├─ _should_fire(task, now) — cron 用 get_prev(now) 比 _next_fire 缓存；
      │  run_at 用 _next_fire[task.id] 一次性 fire 后置位
      └─ _fire_task(task, manual=False)
         └─ run_at 成功后 → tasks_book.mark_run_at_consumed(task_id=task.id)
            （enabled=0；一次性任务绝不二次触发）
4. _fire_task:
   ├─ contract guard: task.conversation_id 必须存在（创建时已分配）；
   │  否则抛 ValueError →  job 被 _handle_run_task_job 翻成 FAILED
   ├─ tasks_book.record_run_start(task_id, manual=manual) — 写 task_runs
   │  + tasks.last_run_at
   ├─ 追加 contextual prompt 为 user 消息到 task 的 conversation
   ├─ publish_chat(text=contextual_prompt, channel="task", ...) → agent_job_board
   └─ AgentWorker._process → 完成后通过 delivery_job_board 投递回复
5. 失败处理: tasks_book.record_run_end("failed") 持久化 consecutive_failures
   + last_error（上限 9999）；超阈值 → 禁用任务 + 创建 ActionItem
```

### 手动 / tool 触发 — `run_task_job_board`（唯一入口）
```
入口: bus.run_task_job_board.publish(RunTaskJob(task_id, manual=True))
      ├─ WebUI "立即运行" 按钮: manual=True
      └─ 未来扩展（其他 tool / cron 子路径）: manual=True / False

任务创建契约（任务进入 cron 之前必须完成）:
  1. conversations_book.create_task_conversation(contact_id, title, ...)
     → 返回 conversation_id
  2. tasks_book.add(..., conversation_id=conv_id, contact_id=...)
  两个调用方（WebUI API + schedule_task LLM tool）共用同一段。

TaskWorker claim 后 _handle_run_task_job → tasks_book.get(task_id) →
_fire_task → tasks_book.record_run_start → ChatNotifyJob 投递 → AgentWorker 跑
（`task_runs.id` 是这次 run 的标识；Agent 侧没有 run 概念，steering 走
Task.conversation_id —— 所有 run 共享同一个会话上下文）

历史路径（已删除，不可用）:
  - TaskChannel.dispatch — 已被 publish RunTaskJob 取代
  - scheduler.submit_now — apscheduler 已删
  - RunTaskJob 上携带 conversation_id / contact_id / fired_by
    — 已删除，由 TaskWorker 从 Task 行读，避免双源不同步
```

**不可改的守卫**:

- task conversation 的 channel 必须是 `"task"`（不是 tg/webui）
- TaskWorker 通过 chat_job_board 发布任务消息，AgentWorker 消费；TaskWorker 不直接调用 Agent.run / 不绑定回调
- 连续失败超阈值必须禁用任务（防止 API key 被无效任务烧光）
- TaskWorker 的 cron 循环跑在主 event loop（与 FastAPI 共享），apscheduler 依赖已删除（tasksBook.py 明确 "no apscheduler dependency"）
- 一次性 `run_at` 任务 fire 后必须 `mark_run_at_consumed`，否则下一次轮询会再次触发
- 任何手动 / tool 触发**只能**走 `run_task_job_board` — 禁止直接调 `_fire_task`
- task conversation 的 `conversation_id` 是 Task 创建时分配的；fire 时由
  TaskWorker 从 `tasks.conversation_id` 读取。job 上不传 —— 单一来源
  防止 caller 把 run 跑到错的会话里

---

## 7. 首次部署与 IM 两步验证

bootstrap 创建 MAGIS 共享的 `MagisAdmin(admin)`，并在 `eva-000` 创建一个仅供
本地数据归属的 Contact 投影（`magis_admin_id`）。用户可从 `127.0.0.1` 的 WebUI
直接进入；没有 onboarding API 或密码模式。

在 Settings 配置 Telegram 后，运行时通过
`/api/access/two-factor/send-login-code` 与
`/api/access/two-factor/verify-login-code` 验证一次性代码并绑定共享管理员身份。
验证码仅存 hash、五分钟过期且验证后立即删除。启用前仅禁止创建新的 MAGIS admin
或 `assigned` user；其他单人使用功能保持可用。

## 8. 登录与 Cookie 身份

**入口**: `magi/channels/api/auth.py`

### 两步骤登录（control-plane 入口）
```
1. POST /auth/send-login-code { contact_id }
   └─ 通过 delivery_job_board.publish(DeliveryJob(channel="tg",
     destination=str(contact.tgid),
     payload={"text": code_text, "contact_id": contact_id}))
     → 对应 channel worker claim loop → 原始 HTTP 发送 6 位码
   └─ 5 分钟 TTL / 60s 冷却（settings_book 持久化在 auth.login_code）
   └─ 发送失败 → 清掉 login_code 回滚

2. POST /auth/verify-login-code { contact_id, code }
   └─ 匹配 → 设置 cookie
     `_sign_selected_session(bus, magi_id, tgid, display_name,
                              admin, assigned)` 返回 `v4.<body>.<sig>`
   └─ Cookie: HTTPOnly + SameSite=Lax + 14 天 TTL + HMAC-SHA256 签名
```

### Cookie 身份模型（两套共存）
```
签名密钥来源（按优先级）:
  1. 控制面启用时：`bus.control_secrets_book.get_by_name(magis_name)` →
     SHA256(row.secret_value + b"magi-control-session")
     （在 `magi init` 时由 `_ensure_control_secret` 写入；运行时不再读 env）
  2. 否则：bus.settings_book.get("auth.signing_key") →
     SHA256(raw + b"magi-session-signing")
  3. 启动兜底：secrets.token_bytes(32)
     （仅极早期 control-plane init 路径）

格式 A（legacy / private-runtime tests）:
  `_sign_contact_id(bus, contact_id)` →
    base64(contact_id:ts:hmac[:16])
  → `_verify_signed_contact_id(token)` → contact_id (int)

格式 B（当前 control-plane 默认；v4）:
  `_sign_selected_session(bus, magi_id, tgid, display_name,
                          admin, assigned)` → `v4.<base64(payload)>.<sig>`
  payload: {v: 4, magi_id, tgid, display_name, admin, assigned, ts}
  → `selected_session(token)` → payload dict
  └─ cookie v2 → v3 升级时把历史 key "magic_id" 改成 "magi_id"；
     v3 → v4 升级时把 "telegram_id" 改成 "tgid"。两次都在部署时
     强制作废旧 cookie（version bump），而不是软兼容。

_super_admins():
  1. 主路径: 读 contacts_book (admin=True) → contact_id 集合
  2. 回退: 旧 telegram.super_admins meta key
     └─ 旧值是 tgid 列表 → 解析为 Contact.id
```

**不可改的守卫**:

- Cookie payload 主键是 `contact_id`（历史 `uid` 即 `Contact.id`），
  **不是** tgid。
- v4 cookie 必须包含 `magi_id`（不是 `magic_id`）— 浏览器不能跨 MAGI
  复用 cookie。
- v4 cookie 必须包含 admin / assigned 标志 — 服务端不再次查表。
- `_super_admins()` 的 ORM 读取失败必须回退到 legacy meta（极早期启动场景）。
- 旧 cookie（pre-v4，payload 用 `magic_id` 或 `telegram_id`）在升级后失效，需重新登录。
- 签名不防文件系统级攻击者（有 state_dir 访问权 = 已拥有 DB）。
- 在 control-plane 模式下，签名密钥统一从 `bus.control_secrets_book`
  派生；`MAGI_RUNTIME_ID` 用于绑定目标 runtime。Runtime 通过同一个
  MAGIS 表读取并验证 HMAC；无 env 注入。

---

## 9. Memory 工具 — 角色门

**入口**: `magi/tools/memory/core_memory/`（add_memory / update_memory / complete_memory / delete_memory）

```
四个工具: add_memory / update_memory / complete_memory / delete_memory
  └─ 仅 admin 和 assigned 可写（_WRITE_ROLES）
  └─ contact / guest → ToolResult(is_error=True)
  └─ 门禁检查: Tool.gate（基类）合并 role + admin
  └─ 两重守卫: 1) registry 过滤工具菜单 2) run() 内防御性再检

读路径: 无 search_memory 工具
  └─ Memory 通过 system_prompt.build_system_prompt 的
    _format_memory_block 在 system prompt 中呈现
  └─ 展示所有 important + ongoing，≤50 条，8KiB body 上限
```

**不可改的守卫**:

- 写操作必须是 admin/assigned 角色，双重守卫不可移除任何一层
- contact/guest 角色绝不能写 memory
- 当前无 search_memory 工具 — 读路径仅 system prompt block

---

## 10. Contact 工具 — Notes 模型

**入口**: `magi/tools/memory/contacts/`
（每个工具一个模块：`add_contact.py` / `add_contact_note.py` /
`update_contact_note.py` / `delete_contact_note.py` /
`update_daily_note.py` / `search_contacts.py`）

```
工具清单:
  - add_contact              — 新建或更新 Contact 主档（owner_id, person_id 唯一）
  - add_contact_note         — 给 Contact 添加一条 note 行
  - update_contact_note      — 按 note_id 更新某条 note
  - delete_contact_note      — 按 note_id 删除某条 note
  - update_daily_note        — upsert 当天 daily note（按 contact_id + date 唯一）
  - search_contacts          — 按 name / display_name 搜索

写门禁（Tool.gate 合并 role + admin；ALLOWED_ROLES = {"admin", "assigned"}）:
  admin=True → 允许
  role="assigned" → 允许
  其他 → 拒绝

add_contact（upsert 语义）:
  └─ 查找 (owner_id, person_id) 唯一对
  └─ 存在 → 累积更新 name / display_name，不创建重复行
  └─ 不存在 → 创建 Contact(role 默认 "assigned"，admin 必须 False)

add/update/delete_contact_note:
  └─ 每个 note 是 contact_notes 表的独立行
  └─ update / delete 必须按 note_id，**不**整体重写 contact

update_daily_note:
  └─ 按 (contact_id, date) upsert — 一天一行
  └─ 读路径：`contact_notes_book.read_daily_note(contact_id)` 返回当天行

format_contact_block（在 system prompt 中渲染）:
  └─ 仅渲染当前对话者 (per-chat)
  └─ 2KB 上限
  └─ WebUI 空 chat_id 跳过
  └─ TG 路径: chat_id → Contact.tgid → Contact
  └─ 使用真实 display_name，不是 person_id FK
```

**不可改的守卫**:

- `(owner_id, person_id)` 唯一约束，不可移除
- `add_contact` 必须是 upsert 语义（累积更新，不创建重复行）
- note 的增删改必须按 `note_id` 走独立工具 — **不能**在 `add_contact`
  里覆盖 notes 列表
- daily note 必须按天 upsert（一天一行）— 不允许任意时间戳多条
- contact block 渲染必须用真实 display_name，绝不显示原始 person_id
- contact / guest 角色**所有** Contact 写工具都被拒（add / note /
  daily note / search 都被 gate）

---

## 11. MCP 工具加载与变更

**入口**: `magi.mcp.worker.McpWorker` + `magi.tools.mcp.*` (manage tools)

```
启动时 (McpWorker.on_start → _bootstrap_connections):
  → bus.mcp_servers_book.list_enabled()  (仅 enabled=True)
  → 并行连接每个 server (MCPServerConnection.connect)
  → 聚合发现工具 → register_tools("mcp", discovered_tools)
  → on_tools_changed → ToolsWorker 自动重发布 catalog

运行时 (McpWorker._run):
  claim change_mcp_server_job_board
    → kind="added"/"updated": 写 Book + 重连 server
    → kind="toggled": flip enabled flag + 重连/断开
    → kind="deleted": delete Book 行 + 断开连接
    → re-inject tools → ToolsWorker 自动重发布 catalog

manage tools 路径 (magi.tools.mcp.*):
  add/update/delete_mcp_server → publish ChangeMCPServerJob
    → wait_for_result() → 等待 McpWorker 处理完成
    → 返回结果给 LLM
```

**不可改的守卫**:

- 仅连接 `enabled=True` 的 server
- 单个 server 连接失败不阻塞其他 server 的引导
- 连接失败保留错误日志，后续收到 "updated" Job 时可重试
- MCP 工具通过 `tools.registry.register_tools` 注入，`ToolsWorker.on_tools_changed` 自动检测并重发布 catalog

---

## 12. 压缩 (Compaction)

**入口**: `magi/agent/compaction.py::maybe_compact(contact_id, conversation_id, messages, bus=...)`

```
触发条件: estimate_messages_tokens(messages) > context_window * threshold_pct%
  └─ 配置项: settings_book.get("compaction.{context_window,
     threshold_pct, keep_tail}")；默认 200_000 / 80% / 8

压缩流程:
  1. 调 LLM 生成旧消息摘要（compact prompt；call_llm_for_summary 通过
     llm_job_board.publish / wait_for_result，phase="auto_compact"）
  2. 归档旧消息: messages_book.add(role="user", ...) 一行 summary 替代；
     保留最近 K 条活跃
  3. messages[:] = [summary_msg] + messages[-keep:]
  4. 失败 → 吞掉，本轮不压缩（不阻塞对话）

FTS5 搜索:
  └─ 搜索活跃消息（默认 include_archived=False）+ 可选 include_archived=true
  └─ 归档行仅供取证
  └─ 由 MessageBook.search + install_conversation_fts_schema 提供
```

**不可改的守卫**:

- 压缩 LLM 调用失败不能阻塞对话（返回 None，本轮跳过）
- 归档消息不能出现在默认搜索结果中

---

## 13. Proactive 系统级策略

**入口**: `magi/proactive/worker.py::ProactiveWorker._run`

```
启动 (ProactiveWorker.on_start → _bootstrap):
  1. _resolve_magis_id() → 查 memberships_book.get(magi_id=self._magi_id)
  2. _is_adam(magis_id) → 比较 magis_book.get(magis_id).adam_id 与 self._magi_id
  3. 若本 MAGI 是某 MAGIS 的 ADAM：
     对该 MAGIS 所有 admin 幂等插入 credentials nudge ActionItem
     （magi.proactive.credentials_action.ensure_for_admin）

主循环 (_run, poll_seconds 默认 0.25):
  while not stopping:
    claim seed_preset_tasks_job_board → handle_seed_job(bus, job)
    └─ 从 prompt_book.task_presets() 读 bundled Markdown preset
    └─ 跑 pure planner（magi.proactive.preset_tasks）
    └─ 插入 per-user Task 行（source = SOURCE_PROACTIVE）
```

**不可改的守卫**:

- 启动顺序：ProactiveWorker 是 `WorkerRegistry` 中**最后**拉起的 Worker，不阻塞 runtime composition root
- credentials nudge 对每个 admin 只插入一次（ensure_for_admin 内部幂等）
- 若本 MAGI 不是任何 MAGIS 的 ADAM，bootstrap 整个跳过

---

## 14. A2A — 同一 MAGIS 内 MAGI ↔ MAGI 协作

**入口**: `magi/tools/comms/message_magi.py`（出站 actor effect）+ `magi/agent/worker.py::AgentWorker._run`（入站 claim + 终态处理）

### 协议：两类单向终态，而非开放式对话
```
A2A 消息模式（取代 expect_reply）:
  - mode="notify"  : 发送事实/进度/提醒；不等待业务回答
                     → 接收 Agent 处理完即 ack，**不**自动回复
  - mode="request" : 发送一个需要一次回答的问题/委派
                     → Agent 最终文本 compare-and-set 写入该请求
                       的唯一 response，最多一次；response 不再携带
                       request / expect_reply 语义
```

收到 `notify` 的 Agent 即使想联络发送者，也必须**主动调用** `message_magi`
创建一条全新消息——不是对原消息的 reply。收到 `request` 的 Agent 由
runtime（而非模型拼接 `reply_to`）完成原请求。超时、拒绝、失败、迟到
response 都是持久状态；迟到 response 不会悄悄重新唤醒已经结束的发送方 run。

### 出站：`message_magi` 工具（持久 actor effect）
```
入口: magi/tools/comms/message_magi.py::message_magi
识别: AgentWorker 将其标记为 persistent actor effect，
       **不**委托给 ToolsWorker 执行

schema:
  {
    "magi_id": int,                # 必须来自协作目录；worker 再做
                                  # 同 MAGIS + 非自身校验
    "mode": "notify" | "request",
    "text": str,
    "deadline_seconds": int=120,   # 仅 request 有意义
  }

执行路径:
  1. 校验 magi_id ∈ 协作目录（list_collaboration_directory 渲染列表）
  2. 校验 source_magi_id / target_magi_id 属同一 MAGIS 且 ≠ 自己
  3. notify → a2a_notify_job_board.publish(target_magi_id, ...)
             → tool_result = {"persisted": True}（不进入等待集）
  4. request → a2a_request_job_board.publish(target_magi_id, ...,
              deadline_at)
             → 加入 _gather_all 等待；唯一 response / 失败 /
               deadline_seconds 任一触发后写入 tool_result
  5. 失败 → ToolResult(is_error=True)，但已落库行不回滚
     （re-deliver 由 receiver 重试语义处理）
```

### 入站：`AgentWorker.claim_next_turn()` 公平消费
```
入口: magi/agent/worker.py::AgentWorker._run
  while not stopping:
    claim_next_turn():
      轮流从以下三类来源取下一条未过期、目标 = self._magi_id 的 job：
        - agent_job_board（本地 chat）
        - a2a_request_job_board（共享 MAGIS）
        - a2a_notify_job_board（共享 MAGIS）
      每类连续消费上限（例如 4 条）→ 防止一侧饥饿
      优先取最早 created_at 的项

A2A 入参 RunContext:
  ctx.channel ∈ {"a2a.notify", "a2a.request"}
                 # 不是公开 channel，是内部来源标识
  ctx.source_magi_id = job.source_magi_id
  ctx.request_id     = job.job_id            # 仅 request
  ctx.mode           = job.mode
  ctx.deadline_at    = job.deadline_at      # 仅 request
  ctx.text           = job.text
```

### 终态处理（无 delivery）
```
A2A run 终态（_process 收尾）:
  - 不写 delivery_job_board（普通最终回答不进任何人类 channel）
  - mode == notify:
      → ack() a2a_notify_job_board 行（compare-and-set 标记已消费）
      → 写 ChatNotifyResult(success=True, status="completed")
  - mode == request:
      → compare-and-set 写 response_payload + response_status
        ∈ {"responded", "rejected", "timed_out", "failed"}（一次）
      → 写 ChatNotifyResult(success=True/False, status="completed"/"failed")
      → **绝不**再生成 A2A reply；response 不是新入站消息
```

### 系统提示中的 MAGIS 协作目录
```
每 turn 注入（与 SOUL → Instructions → Memory → Contact →
Daily note → Skills 六块并行渲染）:
  ## MAGIS collaboration directory
  - <self> [ADAM] eva-frontend  → "负责 WebUI 前端、React Query 与
                                   构建验证；不执行数据库迁移"
  - [EVA]  eva-backend          → "负责 API 设计与数据库迁移；
                                   不直接接触前端"
  - ...

来源: MagisMembershipBook.list_collaboration_directory(magi_id=self._magi_id)
  - 只返回同 MAGIS 成员
  - 每条目含 magi_id / runtime_name / role_name / responsibility
  - 永不暴露其他成员的私有 prompt / API key / 记忆 / 对话内容

responsibility 字段:
  - MagisMembership.responsibility (Text) — 公开、可编辑
  - 由 MAGIS 操作者维护，**不是** LLM 自修改字段
  - WebUI MAGIS membership 创建/更新模型同步增加该字段
  - 变更无需重启 Agent 即可生效（每 turn 重读）
```

### 工具契约的破坏性变更
```
已删除:
  - expect_reply: bool 参数（无法表达循环终止）
  - 模型可控 reply_to 参数
  - HTTP 地址 / adapter / router / transport / protocol 任何参数
  - channels/a2a/{adapter,router,transport,protocol}.py
  - sendA2AJob / A2AWorker 本地 SQLite 路径
  - 在交付后立即把 sender 等待的 job 标成功的旧语义
```

**不可改的守卫**:

- A2A **不能走 HTTP / webhook / 外部签名协议** — 是 MAGIS 共享数据库上的持久 job
- A2A **不能作为 channel** — 没有 channel worker，没有 `delivery_job_board` 行
- A2A boards 必须用 `Bus._magis_factory` 实例化；**绝不**写入任一 MAGI 的 local SQLite
- `notify` 的 tool result 仅为 "persisted"，永不让模型进入等待集
- `request` 一次只能写一次 response（compare-and-set），迟到 response 不复活已结束的发送方 run
- `magi_id` 必须来自协作目录；worker 再做同 MAGIS + 非自身校验
- 不允许向自己发送（同 MAGIS 但 magi_id == self._magi_id → 拒绝）
- `claim_next_turn()` 必须轮流轮询三类 board + 设置连续消费上限
- A2A 输入的 `RunContext.channel` 是 `a2a.notify` / `a2a.request`，不是公开 channel
- 系统提示中的协作目录每 turn 重读；成员职责 / role 更新无需重启 Agent 生效
- 系统提示向模型注入精确规则：不要对通知自动回复；对请求只面向请求内容答复一次；需要继续合作时显式调用 `message_magi` 新建消息
- 运行时强制终态，提示词只帮助模型选择正确协作行为

---

## 16. Hook 子系统（预留设计，尚未实现）

当前仓库**没有** `magi/bus/hooks/` 模块，也没有
`hook_evaluations` / `hook_plugin_configs` 表或对应架构测试。不要把 Hook
当作当前 runtime 的扩展点或依赖它编排业务流程。

未来若引入 Hook，必须单独落地模块、持久化迁移和测试，并保持以下边界：

- Handler 只接收冻结、JSON-safe 的事件 DTO，不接收 `Bus` 或 ORM/session 引用。
- Hook 的外部 I/O 通过 durable Job Board 执行；不能放进 BUS transaction。
- Hook 的声明 scope、授权和失败/重试策略必须有可执行的架构测试。

---

## 改动检查清单

修改上述任何模块前，确认以下不变式：

- [ ] 写操作的角色门禁未被绕过（Tool.gate 合并 role + admin）
- [ ] D.22 通道守卫未被移除（MessageBook.add 写入时检查 channel 匹配）
- [ ] LLM 凭证严格模式未被回退（get_provider 必须 strict mode）
- [ ] LLM 凭证只从 `bus.settings_book` 读取，不从 Contact 表
- [ ] Cookie 值仍为 `contact_id` (int)，非 tgid / magic_id
- [ ] v4 selected-MAGI cookie 必须包含 `magi_id`（不是 `magic_id`）
- [ ] Onboarding 验证码一次性使用（任何路径都 state_delete）
- [ ] TG adapter send 走原始 HTTP，不走 bot.send_message
- [ ] Task runner 不绑定 TG 回调，只发 ChatNotifyJob 给 AgentWorker
- [ ] Memory/Contact 工具的双重角色守卫完整
- [ ] 压缩失败不阻塞对话（maybe_compact 失败时吞掉）
- [ ] system prompt 六块顺序不变：SOUL → Instructions → Memory → Contact → Daily note → Skills
- [ ] Agent Worker 接收 `magi_id` 用于渲染 per-MAGI instruction block
- [ ] ProactiveWorker 是 WorkerRegistry 最后启动的 Worker
- [ ] Provider 配置变更只走 `change_provider_config_job_board`，不直接 `settings_book.set`
- [ ] A2A 不再是 channel：没有 `delivery_job_board` 行 / channel worker / `channels/a2a/` 包
- [ ] A2A boards（`a2a_request_job_board` / `a2a_notify_job_board`）用 `Bus._magis_factory` 实例化；不写入 MAGI local SQLite
- [ ] `notify` 只 ack 不回复；`request` 只能写一次 response（compare-and-set）
- [ ] `message_magi` schema 收敛为 `{magi_id, mode, text, deadline_seconds}`，不出现 `expect_reply` / `reply_to` / HTTP 地址参数
- [ ] `AgentWorker.claim_next_turn()` 公平轮询 `agent_job_board` + 两个 A2A board，per-source 连续消费上限存在
- [ ] A2A 入参 `RunContext.channel` 取 `a2a.notify` / `a2a.request`，普通最终回答不进 `delivery_job_board`
- [ ] 协作目录每 turn 重读；`responsibility` / role 变更无需重启 Agent
- [ ] 协作目录只返回同 MAGIS 成员；不暴露私有 prompt / API key / 记忆 / 对话内容
