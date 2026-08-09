# MAGI 关键业务流程

> 本文档记录核心业务逻辑的**行为不变式**和关键守卫条件。
> 改动这些模块时必须保持以下行为不变，否则会导致生产问题。
>
> **v3 cutover 注**：实现路径已切到 `magi.bus` Job Board 模型（`agent_job_board` /
> `delivery_job_board` / `tool_job_board` / `llm_job_board` / `a2a_job_board`）。
> 旧 `magi.bus.BusStore` / `magi.bus.agent_runs` / `magi.agent.step.run_agent_step`
> 等已删除；本文中的旧函数名作为**行为锚点**（保留对应不变式），不是
> 当前可调用的代码路径。实际入口：
>
> - Agent Loop → `magi/agent/worker.py::AgentWorker._run` → `_process`
> - Channel egress → `magi/channels/worker_base.py::_claim_delivery_loop`（每 channel worker 的 `_run` 拉起自己的循环）
> - Channel ingress → `magi/channels/telegram/worker.py::_on_tg_message` 等
> - Credential 解析 → `magi/providers/factory.py::get_provider(model=None)`
> - Task 调度 → `magi/channels/tasks/worker.py::TaskWorker._run`
> - 手动 / tool 触发任务 → `bus.run_task_job_board.publish(RunTaskJob(...))`（走 `magi/bus/guild/runTaskJob.py`）

---

## 1. Agent Loop — 消息处理主循环

**入口**: `magi.agent.worker.AgentWorker` → `AgentWorker._run` → `_process`

```
1. MCP 工具已由 McpWorker 在启动时引导注入到 registry，运行时通过
   `mcpServerChangedJobBoard` 异步处理变更。Agent Loop 不再主动轮询
   `mcp_servers` 表 — 工具目录始终与 Worker 状态同步。
   └─ `McpWorker.start`: 并行连接所有 enabled server → `register_tools("mcp", ...)`
   └─ 运行时变更: manage tools publish Job → `McpWorker._run` claim → 重连 → re-inject
   └─ `ToolsWorker.on_tools_changed` 自动检测 → re-publish catalog

2. 凭证校验 (`get_provider`，旧的 `_validate_credentials` 已删除)
   └─ `get_provider()` 在 `magi/providers/factory.py` 内自己读当前 MAGI 行
     （所有 runtime 都从直属 MAGIS 公共数据库读取配置；EVA 不再通过
      provider/API key 环境变量绕过数据库）
   └─ 凭证来自 `magic` 表（每行对应一个 MAGI 的 LLM 配置），不是 `Contact` 表 —
     `Contact` 表根本没有 provider/api_key 列
   └─ MAGI 未配置 → `LLMNotConfiguredError` → chat 路由 503
     `magi.llm_credentials_required`
   └─ 严格模式，绝不回退系统默认凭证

3. 构建上下文 (`AgentWorker._build_llm_job`)
   ├─ `provider = get_provider(bus=...)` — 未知 provider 抛 `LLMError`；
     MAGI 未配置抛 `LLMNotConfiguredError`
   ├─ `tools = bus.tool_definitions_book.list_schemas(caller_role, caller_admin)`
   ├─ `messages = build_messages_from_session(uid, session_id, text, bus=)`
     — 从 `sessions_book.get_for_owner` + `messages_book.list_for_session`
     加载历史
   ├─ `soul = read_soul(bus=...)` — 读 `prompt_book.get("soul")` 或
     workspace `SOUL.md` fallback
   └─ `system = build_system_prompt(uid=uid, soul=soul, bus=...)` — 六块顺序
     拼接: SOUL → Instructions → Memory → Contact → Daily note → Skills

4. 工具循环 (`AgentWorker._process` 的 `while iteration < max_iterations`)
   while iteration < max_iterations:
   ├─ [每轮] cancel check (`agent_turn_store.is_cancel_requested`)
   ├─ [每轮] `agent_turn_store.renew_turn_lease()` — 心跳保活
   ├─ [每轮] `llm_job_board.publish(CallLLMJob)` → `wait_for_result(timeout)`
   ├─ [每轮] `agent_turn_store.append_message(role="assistant")` — 落 transcript
   ├─ [每轮] `_split_tools()` → tool_job_board / a2a_job_board publish
   ├─ [每轮] `agent_turn_store.commit_waiting_effects(...)` — 状态机切到
     `waiting_effects`，附 pending steering
   ├─ [每轮] `_gather_all()` — 并发 poll tool / a2a + claim_for_conversation
   ├─ [每轮] `_append_tool_result_user_message()` — 把 tool_result blocks +
     steering 拼成下一轮 user 消息
   └─ 终止: 无 tool_uses / max_iterations exceeded / cancel / LLM failure

5. 终态 (`AgentWorker._process` 的 commit 收尾)
   ├─ 无错误 / 有 reply → `agent_turn_store.commit_terminal()` 原子写：
       assistant transcript + token_usage + delivery_job_board publish +
     ChatJobResult(success=True)
   ├─ 异常 → `commit_terminal_failure(error_code, error_detail)`
   └─ cancel → `commit_terminal_cancelled()`（**不**制造伪造 assistant reply）
```

**不可改的守卫**:

- `get_provider()` 必须是 strict mode — MAGI 未配 provider/api_key → `LLMNotConfiguredError`，**绝不**回退到任何默认凭证；调用方 (`_build_llm_job` / `compact_session` / auto-title worker) **绝不能**接受 provider/api_key 作为参数，必须依赖工厂从直属 MAGIS 公共数据库的 `magic` 表读取。
- session message store 读取失败必须吞掉（不崩溃主循环）
- tool result 必须在拼接新消息前安全截断（否则 Anthropic API 拒绝交错 tool 块）— 阈值 8000 字符
- system prompt 六块顺序不可变：SOUL → Instructions → Memory → Contact → Daily note → Skills
- cancel 不发送伪造 assistant reply（避免污染 transcript）

---

## 2. LLM 凭证解析 (get_provider)

**入口**: `magi/providers/factory.py::get_provider(model=None)`

```
设计原则:
  - magic 表（每行对应一个 MAGI 的 LLM 配置）是所有 runtime 的唯一凭证来源
  - 所有 runtime 通过直属 MAGIS 的 `MAGIS_DATABASE_URL` 读取自己的 provider/API key 配置；
    orchestrator 只注入数据库连接串，不注入 provider/API key
  - Contact 表不存 provider/api_key（Token 消耗记在 Contact 上，但凭证属于 MAGI）
  - 每笔 LLM 调用都按 Contact 记录 token_usage，但用对应 MAGI 的 key 发起

调用链:
  TG bot:    _handle_contact_message → get_provider() (直属 MAGIS DB)
  WebUI:     _resolve_caller_credentials → get_provider() (直属 MAGIS DB)
  Runner:    execute_task → durable agent message → AgentWorker → get_provider() (直属 MAGIS DB)
```

**不可改的守卫**:

- 绝不从 Contact 表读 provider/api_key (列已移除)
- Token 消耗仍记给 Contact (token_usage.uid = contact.id)
- 凭证不完整 → LLMNotConfiguredError,不回退默认值

---

## 3. Session 生命周期与 D.22 通道守卫

**入口**: `magi/bus/library/local/sessionBook.py::SessionBook`

### 创建会话
```
1. validate uid — uid 有效性检查
2. 生成新 session_id（`sess_<uuid>`）
3. delivery_address 默认值:
   ├─ TG: str(telegram_chat_id)
   ├─ WebUI: ""（空字符串）
   └─ task: "<scheduled>"
4. sessions_book.add(session_id, uid, channel, delivery_address, ...)
```

### 追加消息（`messages_book.add`）— D.22 通道守卫
```
1. validate session_id + uid
2. message role 校验 — 仅允许 user / assistant / system / tool
3. 加载 session 行 → 不存在或 uid 不匹配 → SessionNotFoundError
4. D.22 通道检查（写入者负责；读取不检查，同一用户可从 WebUI 浏览 TG 历史）:
   if requested_channel is not None AND sess_row.channel AND sess_row.channel != requested_channel:
       → ChannelMismatchError (HTTP 403)
   └─ 空 channel (legacy 行) 不触发 — 写入者胜
   └─ channel=None 跳过检查（用于回填工具）
5. 事务内: INSERT messages + UPDATE session.updated_at
```

**不可改的守卫**:

- **D.22**: 写入必须检查 channel 匹配，读取不检查（同一用户可从 WebUI 浏览 TG 历史）
- 空/旧 session 的 channel 不拒绝写入（兼容 pre-D.22 数据）
- `delivery_address` 列对 domain 代码不透明 — 只有 channel worker 在 `_deliver_*`
  里解释其值（TG = telegram chat id；WebUI = ""；task = "<scheduled>"）

---

## 4. Telegram 入站消息

**入口**: `magi/channels/telegram/worker.py::_on_tg_message`
（TelegramWorker 在 `start()` 里 `asyncio.gather(_run_inbound, _run_outbound)`，
`_run_inbound` 起 `python-telegram-bot` `Application.start_polling`，并注册
`MessageHandler(filters.ALL, _on_tg_message)`；旧 `bot.py::_on_message` 路径已删）

```
1. 提取 tgid = str(update.effective_chat.id)

2. 身份解析 (_find_contact_by_telegram_id)
   └─ ORM 查 Contact.telegram_id == tgid
   └─ 回退: legacy meta key telegram.user.<tgid>.uid (已弃用)

3. 角色分发:
   ├─ contact_admin=True OR role=="assigned" → 通过，走 agent loop
   │   └─ admin 和 assigned 共享同一处理器 (admin 可在 TG 上与 EVA 聊天)
   ├─ role=="guest" → 拒绝，发送 tgid 发现消息
   └─ 无绑定 → 软自动创建 Contact (role="guest"，admin=False)
       └─ 仍发送 tgid 发现消息，等待管理员提升角色

4. 通过后:
   ├─ resolve_or_create_tg_session → 一个 TG 对话一个持久 session
   │   (`sessions_book.get_or_create_for_channel(uid, channel="tg",
   │    delivery_address=tgid)`)
   ├─ `bus.messages_book.add(session_id, role="user", text=text)` — 落 user
     transcript（D.22 守卫在写入时执行）
   └─ `bus.agent_job_board.publish(ChatJob(
        kind="channel.message.received",
        conversation_id=f"tg:{tgid}",
        payload={"text": text, "channel": "tg", "uid": uid,
                 "session_id": session_id, "caller_role": role})`
```

**Contact.role 枚举 (2024 collapse)**:
- 有效值: `assigned` | `guest` (共 2 个)
- 历史值 `admin` 已被迁移到独立 `admin` 布尔字段 (见第 1 节"凭证校验")
- 历史值 `contact` 已被合并入 `guest` — 历史上两个 role 在所有门控路径上行为完全相同 (都被拒绝),所以合并是无损的。`0001_baseline` 的最终 schema 已不含 `contact` 值 (dev 模式 collapsed baseline,历史迁移见 [docs/database-migrations.md](database-migrations.md))

**不可改的守卫**:

- `guest` 角色必须被拒绝 (不属于此 MAGI 服务范围,等待管理员提升)
- `guest` 软自动创建时 admin 必须为 False
- admin 必须能和 assigned 一样聊天 (不能退化为 v0 的 no-op)
- 会话持久化（`messages_book.add`）必须在发布 `ChatJob` 到 `agent_job_board` 之前完成

---

## 5. Channel 出站消息路由

**入口**: `magi/channels/worker_base.py::_claim_delivery_loop`
（dispatcher.py 已删除；每个 channel worker 各自的 `_run` 拉起自己的 claim loop）

### 出站消息流（每个 channel worker 都遵循）
```
ChannelWorker._claim_delivery_loop(deliver_fn, channel_label):
  1. backpressure check（depth > settings["channels.delivery.max_queue_depth"]
     默认 1000；超过 → 每 channel 每分钟 1 次 warning + 5× poll_seconds 休眠）
  2. delivery_job_board.claim() — 跨 channel FIFO（无 channel filter）
  3. 检查 job.channel == 本 channel_label；不是则 release 给其他 worker
  4. deliver_fn(job) — 实际投递（TG 走原始 HTTP send_text_raw；
     WebUI 写 messages_book；A2A 走 send_a2a_delivery；Task 走 …）
  5. delivery_job_board.submit_result(DeliveryResult(success, error))
  6. 异常 → submit_result(success=False, error=str(exc)[:1024])
     （**不**自己重试；BaseJobBoard._claim 负责 lease 过期后 re-lease，
     上限 MAX_ATTEMPTS=3）
```

（旧 ``send_to_session`` / ``send_to_uid`` dispatcher 路径已删除。
v3 下没有 dispatcher.py；每个 channel worker 直接从
``delivery_job_board`` 拉本 channel 的 job 自己投递。）

### TG 出站实际投递 (magi/channels/telegram/worker.py::_deliver_tg)
```
TelegramWorker._deliver_tg(job: DeliveryJob):
  ├─ bus.settings_book.get("telegram.bot_token")
  ├─ chat_id = int(job.destination)
  ├─ text = job.payload["text"]
  └─ channels.telegram.bot.send_text_raw(token, chat_id, text)
      └─ 走原始 HTTP (非 bot.send_message)
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

### 创建 (schedule_task 工具 / WebUI API)
```
1. 角色门: admin 或 assigned → 可创建
2. 创建 ChatSession(channel="task", delivery_address="<scheduled>")
3. INSERT task 行，关联 session_id
4. cron 字段由 croniter 校验（不再是 apscheduler）；run_at 由
   ``validate_run_at`` 规范化到 UTC trailing-Z ISO
```

### 执行 (magi/channels/tasks/worker.py::TaskWorker._run)
```
1. _rehydrate() — 启动时从 tasks_book 读所有 enabled task 状态
2. _reap_stale_runs() — 调 task_runs.reap_stale(older_than_seconds=300)
   把超时未收尾的 running 行翻成 failed（"abandoned by previous worker"）
3. 轮询:
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

### 手动 / tool 触发 — `runTaskJob` 板（v3 唯一入口）
```
入口: bus.run_task_job_board.publish(RunTaskJob(task_id, manual=True,
                                                 fired_by, session_id, uid))
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
- TaskWorker 通过 chat_job_board 发布任务消息，AgentWorker 消费；
  TaskWorker 不直接调用 Agent.run / 不绑定回调
- 连续失败超阈值必须禁用任务（防止 API key 被无效任务烧光）
- TaskWorker 的 cron 循环跑在主 event loop（与 FastAPI 共享），
  apscheduler 依赖已删除（tasksBook.py 行 809 明确 "no apscheduler dependency"）
- 一次性 `run_at` 任务 fire 后必须 `mark_run_at_consumed`，否则下一次轮询会再次触发
- runTaskJob 板的 `fired_by` 是 closed set — 任何新值都必须先在 TaskWorker
  注册分支再加 publish，否则会被静默吞掉
- 任何手动 / tool 触发**只能**走 runTaskJob 板 — 禁止直接调 `_fire_task`

---

## 7. Onboarding 三步骤流程

**入口**: `magi/channels/api/onboarding.py`

### Step 1: 验证并保存 Bot Token
```
POST /verify-bot { token }
  → 调用 Telegram getMe，返回 {ok, username}
  → 不存储

POST /save-bot { token, username }
  → 写入 settings 表
```

### Step 2: 验证 Admin Chat
```
POST /verify-admin { tgid }
  1. 校验 tgid 为数字
  2. 重发冷却: 60s 内拒绝 (含旧 code 剩余有效期提示)
  3. 生成 6 位随机码
  4. 先持久化 (state_set) → 再发送 (send_text_raw)
     └─ 发送失败 → state_delete 回滚
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

- verify-admin 走原始 HTTP（`send_text_raw`），**不能**经任何 channel worker claim loop
  —— 此时尚无 Contact 行，uid→im_id 映射不存在，dispatcher / worker
  路径都会失败
- 验证码必须先存后发，发送失败回滚删除
- 验证码一次性使用：任何校验路径（成功/不匹配/过期）都必须 state_delete
- save_admin 是唯一写入 admin Contact 的地方，必须幂等

## 8. 登录与 Cookie 身份

**入口**: `magi/channels/api/auth.py`

### 两步骤登录
```
1. POST /auth/send-login-code { uid }
   └─ 通过 delivery_job_board.publish(DeliveryJob(channel, destination, ...)) →
     对应 channel worker claim loop → 原始 HTTP 发送 6 位码
   └─ 5 分钟 TTL / 60s 冷却（settings_book 持久化）

2. POST /auth/verify-login-code { uid, code }
   └─ 匹配 → 设置 magi_session cookie = sign_uid(uid)
   └─ Cookie: HTTPOnly + SameSite=Lax + 14 天 TTL + HMAC 签名
```

### Cookie 身份模型
```
magi_session cookie → _verify_signed_uid(token) → uid (int)
  └─ 过期检查 + HMAC 签名验证
  └─ 签名密钥: SHA256(state_dir + "magi-session-signing")

_super_admins():
  1. 主路径: select Contact where admin=True → uid set
  2. 回退: 旧的 telegram.super_admins meta key
     └─ 旧值是 tgid 列表 → 解析为 Contact.id
```

**不可改的守卫**:

- Cookie 存的是 uid (int)，**不是** tgid/telegram_id
- 签名不防文件系统级攻击者（有 state_dir 访问权 = 已拥有 DB）
- `_super_admins()` 的 ORM 读取失败必须回退到 legacy meta（极早期启动场景）
- 旧 cookie （pre-D.24，值为 tgid）在升级后失效，需重新登录

## 9. Memory 工具 — 角色门

**入口**: `magi/agent/memory/self/tools.py`

```
四个工具: add_memory / update_memory / complete_memory / delete_memory
  └─ 仅 admin 和 assigned 可写 (_WRITE_ROLES)
  └─ contact / guest → ToolResult(is_error=True)
  └─ 门禁检查: caller_role_denied_reason(ctx, _WRITE_ROLES)
  └─ 两重守卫: 1) registry 过滤工具菜单 2) run() 内防御性再检

读路径: 无 search_memory 工具
  └─ Memory 通过 format_memory_block() 在 system prompt 中呈现
  └─ 展示所有 important + ongoing，≤50 条，4KB 上限
```

**不可改的守卫**:

- 写操作必须是 admin/assigned 角色，双重守卫不可移除任何一层
- contact/guest 角色绝不能写 memory
- 当前无 search_memory 工具 — 读路径仅 system prompt block

## 10. Contact 工具 — Upsert 逻辑

**入口**: `magi/agent/memory/contacts/tools.py`

```
三个工具: add_contact / update_contact / delete_contact / search_contacts

写门禁 (_contact_write_allowed):
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
  └─ TG 路径: chat_id → Employee.telegram_id → ContactEntry
  └─ 使用真实 display_name，不是 person_id FK
```

**不可改的守卫**:

- (owner_id, person_id) 唯一约束，不可移除
- add_contact 必须是 upsert 语义（累积更新，不创建重复行）
- contact block 渲染必须用真实 display_name，绝不显示原始 person_id

## 11. MCP 工具加载与变更

**入口**: `magi.mcp.worker.McpWorker` + `magi.tools.mcp.*` (manage tools)

```
启动时 (McpWorker.start):
  _bootstrap_connections()
    → bus.mcp_servers_book.list_enabled()  (仅 enabled=True)
    → 并行连接每个 server (MCPServerConnection.connect)
    → 聚合发现工具 → register_tools("mcp", discovered_tools)
    → on_tools_changed → ToolsWorker 自动重发布 catalog

运行时 (McpWorker._run):
  claim mcpServerChangedJobBoard
    → kind="added"/"updated": write Book + 重连 server
    → kind="toggled": flip enabled flag + 重连/断开
    → kind="deleted": delete Book row + 断开连接
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
- MCP 工具通过 registry.register_tools 注入，ToolsWorker.on_tools_changed 自动检测并重发布 catalog

## 12. 压缩 (Compaction)

**入口**: `magi/agent/compaction.py::maybe_compact(uid, session_id, messages, bus=None)`

```
触发条件: estimate_messages_tokens(messages) > context_window * threshold_pct%
  └─ 配置项（new_bus 路径）: settings_book.get("compaction.{context_window,
     threshold_pct, keep_tail}")；Phase 1 默认 200_000 / 80% / 8

压缩流程:
  1. 调 LLM 生成旧消息摘要（compact prompt；call_llm_for_summary 通过
     llm_job_board.publish / get_result）
  2. 归档旧消息: messages_book.add(role="user", ...) 一行 summary 替代；
     保留最近 K 条活跃
  3. messages[:] = [summary_msg] + messages[-keep:]
  4. 失败 → 吞掉，本轮不压缩（不阻塞对话）

FTS5 搜索:
  └─ 搜索活跃消息 (默认 include_archived=False) + 可选 include_archived=true
  └─ 归档行仅供取证
```

**不可改的守卫**:

- 压缩 LLM 调用失败不能阻塞对话（返回 None，本轮跳过）
- 归档消息不能出现在默认搜索结果中

---

## 改动检查清单

修改上述任何模块前，确认以下不变式：

- [ ] 写操作的角色门禁未被绕过
- [ ] D.22 通道守卫未被移除
- [ ] Credential 严格模式未被回退
- [ ] LLM 凭证只从 magic 表读取，不从 Contact 表
- [ ] Cookie 值仍为 uid (int)，非 tgid
- [ ] Onboarding 验证码一次性使用
- [ ] TG adapter send 走原始 HTTP，不走 bot.send_message
- [ ] Task runner 不绑定 TG 回调
- [ ] Memory/Contact 工具的双重角色守卫完整
- [ ] 压缩失败不阻塞对话
- [ ] system prompt 四 block 顺序不变
