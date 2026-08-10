# MAGI 关键业务流程

> 本文档记录核心业务逻辑的**行为不变式**和关键守卫条件。
> 改动这些模块时必须保持以下行为不变，否则会导致生产问题。
>
> **当前 cutover**：所有运行时路径都走 `magi.bus` Job Board 模型
> （`agent_job_board` / `delivery_job_board` / `tool_job_board` /
> `llm_job_board` / `a2a_job_board` / `mcp_server_changed_job_board` /
> `seed_preset_tasks_job_board` / `change_provider_config_job_board` /
> `run_task_job_board`）。旧的 `magi.bus.BusStore` / `agent_turn_store`
> / `magi.agent.step.run_agent_step` 等已删除；文档里残留的旧符号
> 仅作**行为锚点**参考，不是当前可调用路径。
>
> **实际入口**：
>
> - Agent Loop → `magi/agent/worker.py::AgentWorker._run` → `_process`
> - Channel egress → `magi/channels/worker_base.py::_claim_delivery_loop`（每个 channel worker 的 `_run` 拉起自己的循环）
> - Channel ingress → `magi/channels/telegram/worker.py::_on_tg_message` 等
> - Credential 解析 → `magi/providers/factory.py::get_provider(bus=...)`
> - Task 调度 → `magi/channels/tasks/worker.py::TaskWorker._run`
> - 手动 / tool 触发任务 → `bus.run_task_job_board.publish(RunTaskJob(...))`（走 `magi/bus/guild/runTaskJob.py`）
> - 系统级主动策略 → `magi/proactive/worker.py::ProactiveWorker._run`
> - 外部数据流 → `magi/connectors/`（按需启动，非默认 Worker）
>
> **命名约定**（详见 [design/id-naming-standard.md](design/id-naming-standard.md)）：本文件中 `contact_id` 对应历史 `uid`，`magi_id` 对应历史 `magic_id`，`tgid` 对应历史 `telegram_id`，`conversation_id` 对应历史 `session_id`。

---

## 1. Agent Loop — 消息处理主循环

**入口**: `magi.agent.worker.AgentWorker` → `AgentWorker._run` → `_process`

```
1. 工具目录同步
   ├─ MCP 工具由 McpWorker 在启动时引导注入到 registry；
   │  运行时通过 mcp_server_changed_job_board 异步处理变更
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
   │   │   SOUL.md，否则回退到 bundled soul.md
   │   ├─ Instructions = runtime_instruction_block(bus, magi_id=...)
   │   │   — 含 personal instruction + team/role 层（从 MAGIS
   │   │   memberships_book 读）
   │   ├─ Memory = bus.memory_book.list_by_owner(contact_id)
   │   ├─ Contact = bus.contacts_book.get + contact_notes_book
   │   │   .list_for_contact + read_daily_note
   │   └─ Skills = bus.skills_book.list()（file-backed，两根目录）
   └─ tools = bus.tool_definitions_book.list_enabled(caller_role)

4. 工具循环 (AgentWorker._process 的 for _ in range(max_iterations))
   for _ in range(max_iterations):
   ├─ [每轮] cancel check (ctx.cancel_event.is_set())
   ├─ [每轮] llm_job_board.publish(CallLLMJob) → wait_for_result
   │   （默认 120s；失败 → final_error="llm_timeout"/"llm_failed"，
   │    publish delivery → return）
   ├─ [每轮] record_token_usage（按 contact_id 入账 token_usage_book）
   ├─ [每轮] _split_tools(ctx, tool_uses) → tool_jobs / a2a_jobs
   ├─ [每轮] _publish_effects(split) → 收集 tool_call_id → job_id
   ├─ [每轮] _gather_all(ctx, split, tool_ids) — 并发 poll tool /
   │   a2a + claim_for_conversation 拾 steering
   ├─ [每轮] _append_tool_result_user_message() — 把 tool_result blocks
   │   + steering 拼成下一轮 user 消息
   ├─ [LLM_REQUEST_PREPARED + LLM_RESPONSE_RECEIVED hook gates]
   └─ 终止: 无 tool_uses / max_iterations exceeded / cancel / LLM failure

5. 终态 (AgentWorker._process 的 commit 收尾)
   ├─ 无错误 / 有 reply → delivery_job_board.publish(DeliveryJob(
   │     channel=ctx.channel, payload={text, conversation_id,
   │     contact_id})) → channel worker claim → 投递
   ├─ 异常 → ctx.final_error = "agent_crashed" + publish delivery
   └─ cancel → ctx.final_reply = "任务已取消。" + publish delivery
     （**不**制造伪造 assistant reply — 避免污染 transcript）
   └─ ChatJobResult(success=True/False, status="completed"/"failed")
     写回 agent_job_board

6. 后台 (fire-and-forget)
   └─ _maybe_title → spawn request_session_title（独立 task）
```

**不可改的守卫**:

- `get_provider(bus=..., model=...)` 必须是 strict mode — MAGI 未配 provider/api_key → `LLMNotConfiguredError`，**绝不**回退到任何默认凭证；调用方 **绝不能**接受 provider/api_key 作为参数，必须依赖工厂从 `bus.settings_book` 读取。
- session message store 读取失败必须吞掉（不崩溃主循环）
- tool result 必须在拼接新消息前安全截断（否则 Anthropic API 拒绝交错 tool 块）— 阈值 8000 字符
- system prompt 六块顺序不可变：SOUL → Instructions → Memory → Contact → Daily note → Skills
- cancel 不发送伪造 assistant reply（避免污染 transcript）
- AgentWorker 是 `chatJobBoard` 的唯一消费者；不接 `run_agent_step` / `agent.run` 类的 in-process helper

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
  TG bot:    _on_tg_message → _resolve_contact → ChatJob →
             AgentWorker → get_provider() (bus.settings_book)
  WebUI:     /api/chat/send → publish ChatJob → AgentWorker →
             get_provider() (bus.settings_book)
  Runner:    TaskWorker._fire_task → publish ChatJob → AgentWorker →
             get_provider() (bus.settings_book)
```

**不可改的守卫**:

- 绝不从 Contact 表读 provider/api_key（列已移除）
- Token 消耗仍记给 Contact（`token_usage.contact_id = ctx.contact_id`）
- 凭证不完整 → `LLMNotConfiguredError`，不回退默认值
- 未知 provider id → `LLMError`（含已知厂商列表）
- `get_provider()` 是 `providers.worker.ProvidersWorker` 的唯一凭据来源；其它路径必须走工厂

---

## 3. Session / Conversation 生命周期与 D.22 通道守卫

**入口**: `magi/bus/library/local/conversationBook.py::{ConversationBook, MessageBook}`

### 创建会话
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
- 空/旧 session 的 channel 不拒绝写入（兼容 pre-D.22 数据）
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

2. 身份解析 (_resolve_contact)
   └─ ORM 查 Contact.telegram_id == tgid
   └─ 返回 (contact_id, role, admin) 三元组

3. 角色分发:
   ├─ admin=True OR role=="assigned" → 通过，走 agent loop
   │   └─ admin 和 assigned 共享同一处理器 (admin 可在 TG 上与 MAGI 聊天)
   ├─ role=="guest" → 拒绝，发送 tgid 发现消息
   └─ 无绑定 → 软自动创建 Contact (role="guest"，admin=False)
       └─ 仍发送 tgid 发现消息，等待管理员提升角色

4. 通过后:
   ├─ resolve_or_create_tg_session → 一个 TG 对话一个持久 conversation
   │   (sessions_book.get_or_create_for_channel(contact_id, channel="tg",
   │    delivery_address=tgid))
   ├─ bus.messages_book.add(conversation_id, role="user", text=text) —
   │   落 user transcript（D.22 守卫在写入时执行）
   └─ bus.agent_job_board.publish(ChatJob(
        kind="channel.message.received",
        conversation_id=f"tg:{tgid}",
        payload={"text": text, "channel": "tg", "contact_id": contact_id,
                 "conversation_id": conversation_id, "caller_role": role},
        event_id=f"telegram:{tgid}:{message.message_id}",
      ))
```

**Contact.role 枚举 (2024 collapse)**:
- 有效值: `assigned` | `guest`（共 2 个）
- 历史值 `admin` 已被迁移到独立 `admin` 布尔字段（见第 2 节"凭证校验"）
- 历史值 `contact` 已被合并入 `guest` — 历史上两个 role 在所有门控路径上行为完全相同（都被拒绝），所以合并是无损的

**不可改的守卫**:

- `guest` 角色必须被拒绝（不属于此 MAGI 服务范围，等待管理员提升）
- `guest` 软自动创建时 admin 必须为 False
- admin 必须能和 assigned 一样聊天（不能退化为 v0 的 no-op）
- 会话持久化（`messages_book.add`）必须在发布 `ChatJob` 到 `agent_job_board` 之前完成
- `event_id` 形如 `telegram:<tgid>:<message_id>`，提供去重幂等性

---

## 5. Channel 出站消息路由

**入口**: `magi/channels/worker_base.py::_claim_delivery_loop`
（dispatcher.py 已删除；每个 channel worker 各自的 `_run` 拉起自己的 claim loop）

### 出站消息流（每个 channel worker 都遵循）
```
ChannelWorker._claim_delivery_loop(deliver_fn, channel_label):
  1. backpressure check（depth > settings["channels.delivery.max_queue_depth"]
     默认 1000；超过 → 每 channel 每分钟 1 次 warning + 5× poll_seconds 休眠）
  2. delivery_job_board.claim() — 全局 FIFO（无 channel filter；
     所有 channel worker 共享一张 board）
  3. 检查 job.channel == 本 channel_label；不是则 release 给其他 worker
  4. deliver_fn(job) — 实际投递
     （TG → 原始 HTTP send_text_raw；
      WebUI → 写 messages_book；
      A2A → send_a2a_delivery；
      Task → _fire_task → publish ChatJob，delivery 由下游 channel 处理）
  5. delivery_job_board.submit_result(DeliveryResult(success, error))
  6. 异常 → submit_result(success=False, error=str(exc)[:1024])
     （**不**自己重试；BaseJobBoard._claim 负责 lease 过期后 re-lease，
     上限 MAX_ATTEMPTS=3）
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
   │  └─ _handle_run_task_job(rj) → _fire_task(task, fired_by=rj.fired_by, ...)
   │     └─ rj.fired_by ∈ {cron_tick, run_at_consume, api_manual_run,
   │                       schedule_task_tool}（closed set）
   └─ tasks_book.list_all_enabled_for_workers() — cron / run_at tick
      ├─ _should_fire(task, now) — cron 用 get_prev(now) 比 _next_fire 缓存；
      │  run_at 用 _next_fire[task.id] 一次性 fire 后置位
      └─ _fire_task(task, fired_by="cron_tick" / "run_at_consume")
         └─ run_at 成功后 → tasks_book.mark_run_at_consumed(task_id=task.id)
            （enabled=0；一次性任务绝不二次触发）
4. _fire_task:
   ├─ tasks_book.record_run_start(task_id, trigger=fired_by) — 写 task_runs
   │  + tasks.last_run_at
   ├─ 追加 contextual prompt 为 user 消息到 task 的 session
   ├─ publish ChatJob(kind="task.triggered", payload={...}) → agent_job_board
   └─ AgentWorker._process → 完成后通过 delivery_job_board 投递回复
5. 失败处理: tasks_book.record_run_end("failed") 持久化 consecutive_failures
   + last_error（上限 9999）；超阈值 → 禁用任务 + 创建 ActionItem
```

### 手动 / tool 触发 — `run_task_job_board`（唯一入口）
```
入口: bus.run_task_job_board.publish(RunTaskJob(task_id, manual=True,
                                                 fired_by, session_id, contact_id))
      ├─ WebUI "立即运行" 按钮: fired_by="api_manual_run"
      └─ schedule_task LLM tool 创建 one-shot 后回灌: fired_by="schedule_task_tool"

TaskWorker claim 后 _handle_run_task_job → _fire_task → tasks_book.record_run_start
→ ChatJob 投递 → AgentWorker 跑 → 完成后 _fire_task 写 run_id + tasks_book.record_run_end

历史路径（已删除，不可用）:
  - TaskChannel.dispatch — 已被 publish RunTaskJob 取代
  - scheduler.submit_now — apscheduler 已删
```

**不可改的守卫**:

- task session 的 channel 必须是 `"task"`（不是 tg/webui）
- TaskWorker 通过 chat_job_board 发布任务消息，AgentWorker 消费；TaskWorker 不直接调用 Agent.run / 不绑定回调
- 连续失败超阈值必须禁用任务（防止 API key 被无效任务烧光）
- TaskWorker 的 cron 循环跑在主 event loop（与 FastAPI 共享），apscheduler 依赖已删除（tasksBook.py 明确 "no apscheduler dependency"）
- 一次性 `run_at` 任务 fire 后必须 `mark_run_at_consumed`，否则下一次轮询会再次触发
- `run_task_job_board` 的 `fired_by` 是 closed set — 任何新值都必须先在 TaskWorker 注册分支再加 publish，否则会被静默吞掉
- 任何手动 / tool 触发**只能**走 `run_task_job_board` — 禁止直接调 `_fire_task`

---

## 7. Onboarding 三步骤流程

**入口**: `magi/channels/api/onboarding.py`

### Step 1: 验证并保存 Bot Token
```
POST /verify-bot { token }
  → 调用 Telegram getMe，返回 {ok, username}
  → 不存储

POST /save-bot { token, username }
  → 写入 settings_book
```

### Step 2: 验证 Admin Chat
```
POST /verify-admin { tgid }
  1. 校验 tgid 为数字
  2. 重发冷却: 60s 内拒绝 (含旧 code 剩余有效期提示)
  3. 生成 6 位随机码
  4. 先持久化 (settings_book) → 再发送 (send_text_raw)
     └─ 发送失败 → 回滚（删除该 code）
  5. 通过原始 HTTP 发送 (send_text_raw，不用 bot.send_message)
     └─ 原因: 初始安装时 bot 可能未启动

POST /verify-admin-code { tgid, code }
  1. 过期检查 (5 分钟 TTL)
  2. 一次性: 任何路径都 state_delete (防暴力破解)
  3. 成功后不持久化 — 仅返回 display_name
     └─ 最终绑定在 save_admin 完成
```

### Step 3: 保存管理员
```
POST /save-admin { tgids: list[str] }
  1. 校验每个 tgid 为数字
  2. 逐条: resolve (getChat) → upsert Contact(telegram_id, role, admin=True)
  3. 幂等
```

**不可改的守卫**:

- verify-admin 走原始 HTTP（`send_text_raw`），**不能**经任何 channel worker claim loop — 此时尚无 Contact 行，uid→im_id 映射不存在，dispatcher / worker 路径都会失败
- 验证码必须先存后发，发送失败回滚删除
- 验证码一次性使用：任何校验路径（成功/不匹配/过期）都必须 state_delete
- save_admin 是唯一写入 admin Contact 的地方，必须幂等

## 8. 登录与 Cookie 身份

**入口**: `magi/channels/api/auth.py`

### 两步骤登录
```
1. POST /auth/send-login-code { contact_id }
   └─ 通过 delivery_job_board.publish(DeliveryJob(channel, destination, ...)) →
     对应 channel worker claim loop → 原始 HTTP 发送 6 位码
   └─ 5 分钟 TTL / 60s 冷却（settings_book 持久化）

2. POST /auth/verify-login-code { contact_id, code }
   └─ 匹配 → 设置 magi_session cookie = sign_contact_id(contact_id)
     （contact_id:timestamp:hmac[:16]）
   └─ Cookie: HTTPOnly + SameSite=Lax + 14 天 TTL + HMAC 签名
   └─ 签名密钥: SHA256(settings_book["auth.signing_key"] +
      b"magi-session-signing")
```

### Cookie 身份模型
```
magi_session cookie → _verify_signed_contact_id(token) → contact_id (int)
  └─ 过期检查 + HMAC 签名验证
  └─ 签名密钥: SHA256(state_dir + "magi-session-signing")

_super_admins():
  1. 主路径: select Contact where admin=True → contact_id set
  2. 回退: 旧的 telegram.super_admins meta key
     └─ 旧值是 tgid 列表 → 解析为 Contact.id
```

**不可改的守卫**:

- Cookie 存的是 contact_id (int)，**不是** tgid/telegram_id
- 签名不防文件系统级攻击者（有 state_dir 访问权 = 已拥有 DB）
- `_super_admins()` 的 ORM 读取失败必须回退到 legacy meta（极早期启动场景）
- 旧 cookie（pre-D.24，值为 tgid）在升级后失效，需重新登录
- `MAGI_CONTROL_SECRET` 存在时优先（control plane 用同一 secret 派生签名密钥）

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

## 10. Contact 工具 — Upsert 逻辑

**入口**: `magi/tools/memory/contacts/`

```
工具: add_contact / update_contact / delete_contact / search_contacts

写门禁（Tool.gate 合并 role + admin）:
  admin=True → 允许
  role="assigned" → 允许
  其他 → 拒绝

add_contact:
  └─ 查找 (owner_id, person_id) 唯一对
  └─ 存在 → upsert (累积 notes)，不创建重复行

format_contact_block:
  └─ 仅渲染当前对话者 (per-chat)
  └─ 2KB 上限
  └─ WebUI 空 chat_id 跳过
  └─ TG 路径: chat_id → Contact.telegram_id → Contact
  └─ 使用真实 display_name，不是 person_id FK
```

**不可改的守卫**:

- (owner_id, person_id) 唯一约束，不可移除
- add_contact 必须是 upsert 语义（累积更新，不创建重复行）
- contact block 渲染必须用真实 display_name，绝不显示原始 person_id

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
  claim mcp_server_changed_job_board
    → kind="added"/"updated": 写 Book + 重连 server
    → kind="toggled": flip enabled flag + 重连/断开
    → kind="deleted": delete Book 行 + 断开连接
    → re-inject tools → ToolsWorker 自动重发布 catalog

manage tools 路径 (magi.tools.mcp.*):
  add/update/delete_mcp_server → publish McpServerChangedJob
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
    └─ 从 prompt_book.task_presets() 读 bundled YAML preset
    └─ 跑 pure planner（magi.proactive.preset_tasks）
    └─ 插入 per-user Task 行（source = SOURCE_PROACTIVE）
```

**不可改的守卫**:

- 启动顺序：ProactiveWorker 是 `WorkerRegistry` 中**最后**拉起的 Worker，不阻塞 runtime composition root
- credentials nudge 对每个 admin 只插入一次（ensure_for_admin 内部幂等）
- 若本 MAGI 不是任何 MAGIS 的 ADAM，bootstrap 整个跳过

---

## 14. Connector 外部数据流

**入口**: `magi/connectors/`（非默认 Worker，按需启用）

```
生命周期:
  1. Operator 通过 WebUI 添加 ConnectorConfig 行（POST /api/connectors）
  2. Runtime boot 调 connectors.registry.load_connectors() 读 enabled configs
  3. 对每个 config 构造 Connector，await connector.connect()
  4. Connector 订阅上游，发出 ConnectorEvent 到 EventBus
  5. Domain code 通过 bus.subscribe(kind, handler) 消费
  6. Shutdown 时 unload_all() 调 await connector.disconnect()

事件总线 (magi.connectors.bus.EventBus):
  └─ 进程内 asyncio pub/sub；dedup window 默认 5s（防 webhook 重投）
  └─ 与 plugins 子系统共享（magi.plugins.bus re-exports 同 primitives）
  └─ Handlers MUST NOT raise — bus catches + logs，不重试

LLM 调用方式:
  └─ LLM 不直接调 Connector；调包装 connector 的 tool wrapper
  └─ Tool wrapper 调 await connector.fetch(query) → JSON-serialisable dict
```

**不可改的守卫**:

- Connector 协议是 `connect / disconnect / fetch / name / config`，小且稳定
- Connector 异步、长跑任务在 `connect()` 内 spawn，`disconnect()` 内 cancel
- EventBus 不跨进程；跨 MAGI 事件共享走 a2a（未来）

---

## 15. Hook 子系统（BUS-centric）

**入口**: `magi/bus/hooks/`（HookEnvelope + HookDataScope + 11 钩子点）

```
Hook 点（v1）:
  agent.input.pending
  llm.request.prepared
  llm.response.received
  tool.call.pending
  tool.result.received
  a2a.invocation.pending
  a2a.result.received
  delivery.pending
  run.transition.committed
  operation.failed
  operation.dead_lettered

钩子生命周期:
  1. Hook 在 hook_plugin_configs 注册 + 声明所需 HookDataScope
  2. BUS 触发时构造 frozen HookEnvelope（只含声明的 scopes）
  3. Handler 拿到的是 Envelope — **绝不能**接收 Bus 引用
  4. Tool worker gate on TOOL_CALL_PENDING；Agent step gate on LLM_REQUEST_PREPARED

持久化:
  └─ hook_evaluations + hook_plugin_configs 两张表（Alembic 0003 / 0004）
  └─ 架构测试 test_hook_import_boundaries.py / test_hook_envelope_purity.py
     强制边界
```

**不可改的守卫**:

- Handler **绝不能**接收 `Bus` 引用 — Envelope 是唯一输入
- Envelope 是 frozen（不可变），防止 handler 污染上游状态
- 历史 `magi.plugins.bus` 已被移除；hooks 完全 BUS-centric

---

## 改动检查清单

修改上述任何模块前，确认以下不变式：

- [ ] 写操作的角色门禁未被绕过（Tool.gate 合并 role + admin）
- [ ] D.22 通道守卫未被移除（MessageBook.add 写入时检查 channel 匹配）
- [ ] LLM 凭证严格模式未被回退（get_provider 必须 strict mode）
- [ ] LLM 凭证只从 `bus.settings_book` 读取，不从 Contact 表
- [ ] Cookie 值仍为 `contact_id` (int)，非 tgid
- [ ] Onboarding 验证码一次性使用（任何路径都 state_delete）
- [ ] TG adapter send 走原始 HTTP，不走 bot.send_message
- [ ] Task runner 不绑定 TG 回调，只发 ChatJob 给 AgentWorker
- [ ] Memory/Contact 工具的双重角色守卫完整
- [ ] 压缩失败不阻塞对话（maybe_compact 失败时吞掉）
- [ ] system prompt 六块顺序不变：SOUL → Instructions → Memory → Contact → Daily note → Skills
- [ ] Agent Worker 接收 `magi_id` 用于渲染 per-MAGI instruction block
- [ ] Hook handlers 不持有 Bus 引用（仅 HookEnvelope 输入）
- [ ] ProactiveWorker 是 WorkerRegistry 最后启动的 Worker