# MAGI 关键业务流程

> 本文档记录核心业务逻辑的精确执行顺序和关键守卫条件。
> 改动这些模块时必须保持以下行为不变，否则会导致生产问题。

---

## 1. Agent Loop — 消息处理主循环

**入口**: `magi.agent.worker.AgentWorker` → `magi.agent.step.run_agent_step()`

```
1. MCP 懒重载 (maybe_reload_mcp_tools)
   └─ 仅当 mcp_servers 表 MAX(updated_at) 变化时才重建子进程
   └─ 失败吞掉，保留现有缓存

2. 凭证校验 (get_provider, _validate_credentials 已被删除)
   └─ get_provider() 在 magi/providers/factory.py 内自己读当前 MAGI 行
     （所有 runtime 都从直属 MAGIS 公共数据库读取配置；EVA 不再通过 provider/API key 环境变量绕过数据库）
   └─ 凭证来自 magic 表（每行对应一个 MAGI 的 LLM 配置），不是 Contact 表 — Contact 表根本没有 provider/api_key 列
   └─ MAGI 未配置 → LLMNotConfiguredError → chat 路由 503 magi.llm_credentials_required
   └─ 严格模式，绝不回退系统默认凭证

3. 构建上下文 (_build_context)
   ├─ get_provider() — 未知 provider 抛 LLMError；MAGI 未配置抛 LLMNotConfiguredError
   ├─ ToolContext(state_dir, workspace, uid, channel, session_id)
   ├─ _build_messages_from_session() — 从 SessionStore 加载历史 → (messages, seen_message_ids)
   ├─ read_soul(state_dir) — 读 SOUL.md
   └─ get_tool_schemas(caller_role, caller_admin) — 按角色过滤工具列表

4. 工具循环 (_run_tool_loop)
   while iterations_run < max_iter:
   ├─ [每轮] _drain_pending_user_messages() → 有新用户消息则重置 iterations_run=0
   ├─ [每轮] maybe_compact() → 超阈值则压缩
   ├─ [每轮] provider.chat(system=build_system_prompt(...), messages=..., tools=...)
   │   └─ system prompt 拼装: SOUL → memory_block → contact_block → skills_block
   ├─ [每轮] _run_tool_calls() → 逐个执行 tool.run()
   │   └─ 未知工具 → is_error=True
   │   └─ 崩溃 → 捕获，包装 is_error=True
   │   └─ 结果截断至 8000 字符
   └─ 终止: 无 tool_uses 或 stop_reason=="end_turn"

5. 审计与返回 (_audit_and_return)
   ├─ record_token_usage() → 写入 token_usage 表
   └─ 返回 final_text 或 fallback_reply
```

**不可改的守卫**:

- `get_provider()` 必须是 strict mode — MAGI 未配 provider/api_key → `LLMNotConfiguredError`，**绝不**回退到任何默认凭证；调用方 (`_build_context` / `compact_session` / auto-title worker) **绝不能**接受 provider/api_key 作为参数，必须依赖工厂从直属 MAGIS 公共数据库的 `magic` 表读取。
- `_drain_pending_user_messages` 的 store 读取失败必须吞掉（不崩溃主循环）
- `_truncate_at_safe_boundary` 在拼接新消息前必须调用（否则 Anthropic API 拒绝交错 tool 块）
- `_run_tool_calls` 的结果必须截断到 8000 字符
- system prompt 四个 block 的顺序不可变：SOUL → memory → contact → skills

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

**入口**: `magi/agent/memory/session/store.py::SessionStore`

### 创建会话 (create)
```
1. _validate_uid(uid) — uid 有效性检查
2. new_session_id() — 生成唯一 session_id
3. delivery_address 默认 "12345" (legacy compat)
   ├─ TG 调用者: str(effective_chat.id)
   ├─ WebUI: "" (空字符串)
   └─ scheduled: "<scheduled>"
4. INSERT ChatSession(uid, channel, delivery_address, ...)
```

### 追加消息 (append_messages) — D.22 通道守卫
```
1. _validate_session_id + _validate_uid
2. message role 校验 — 仅允许 _ALLOWED_MESSAGE_ROLES
3. 加载 session 行 → 不存在或 uid 不匹配 → SessionNotFoundError
4. D.22 通道检查:
   if channel is not None AND sess_row.channel AND sess_row.channel != channel:
       → ChannelMismatchError (HTTP 403)
   └─ 空 channel (legacy 行) 不触发 — 写入者胜
   └─ channel=None 跳过检查 (用于回填工具)
5. 事务内: INSERT messages + UPDATE session.updated_at
```

**不可改的守卫**:

- **D.22**: 写入必须检查 channel 匹配，读取不检查（同一用户可从 WebUI 浏览 TG 历史）
- 空/旧 session 的 channel 不拒绝写入（兼容 pre-D.22 数据）
- `delivery_address` 列对 domain 代码不透明 — 只有 dispatcher/adapter 解释其值

---

## 4. Telegram 入站消息

**入口**: `magi/channels/telegram/bot.py::_on_message()`

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
   ├─ SessionStore.append_messages(用户消息) → D.22 守卫
   └─ publish AgentMessage(uid, session_id, channel="tg", caller_role=contact_role)
```

**Contact.role 枚举 (2024 collapse)**:
- 有效值: `assigned` | `guest` (共 2 个)
- 历史值 `admin` 已被迁移到独立 `admin` 布尔字段 (见第 1 节"凭证校验")
- 历史值 `contact` 已被合并入 `guest` — 历史上两个 role 在所有门控路径上行为完全相同 (都被拒绝),所以合并是无损的。`0001_baseline` 的最终 schema 已不含 `contact` 值 (dev 模式 collapsed baseline,历史迁移见 [docs/database-migrations.md](database-migrations.md))

**不可改的守卫**:

- `guest` 角色必须被拒绝 (不属于此 MAGI 服务范围,等待管理员提升)
- `guest` 软自动创建时 admin 必须为 False
- admin 必须能和 assigned 一样聊天 (不能退化为 v0 的 no-op)
- 会话持久化必须在发布 `AgentMessage` 之前完成

---

## 5. Channel Dispatcher — 出站消息路由

**入口**: `magi/channels/dispatcher.py`

### send_to_session(session_id, text)
```
1. 加载 ChatSession 行
2. 如果是 WebUI → 直接追加消息到 session store (inline，无需 adapter)
3. 其他通道 → 查找注册的 adapter → adapter.send(uid, text)
```

### send_to_uid(uid, channel, text)
```
1. 查找注册的 adapter
2. adapter.lookup_im_id(uid) → 无绑定则 RuntimeError
3. adapter.send(uid, text)
```

### TG Adapter 发送 (magi/channels/telegram/adapter.py)
```
TelegramAdapter.send(uid, text):
  ├─ lookup_im_id(uid) → Contact.telegram_id
  └─ send_text_auto(chat_id_int, text)
      └─ 走原始 HTTP (非 bot.send_message)
      └─ 原因: bot 实例绑定 daemon 线程的 event loop，
         从 WebUI 的 loop 调用会静默丢弃
```

**不可改的守卫**:

- WebUI 路径**不走 adapter**（直接写 session store，用户 inline 看到）
- TG adapter 的 `send()` **必须走原始 HTTP**（`send_text_auto`），不能用 `bot.send_message`
- Adapter 在 import 时自注册，在 dispatcher 首次调用时懒加载
- Domain 代码（tools/runner/webui api）绝不直接读 `delivery_address` 或调用 adapter

---

## 6. 定时任务 — 创建与执行

### 创建 (schedule_task 工具 / WebUI API)
```
1. 角色门: admin 或 assigned → 可创建
2. 创建 ChatSession(channel="task", delivery_address="<scheduled>")
3. INSERT task 行，关联 session_id
4. 注册到 apscheduler (cron/interval)
```

### 执行 (magi/channels/tasks/runner.py::execute_task)
```
1. 加载 Task 行 + Contact 凭证
   └─ 无 session_id 的旧行 → 首次触发时分配
2. 追加 prompt 为新的用户消息到 task 的 session
3. publish AgentMessage(uid, session_id, channel="task")
4. Agent 的 send_message 工具通过 dispatcher 推送到指定通道
   └─ 跑者不绑定回调 — 路由完全由工具内部处理
5. 拉取最新 token_usage → 写入 TaskRun
6. 失败处理: consecutive_failures++ → 超阈值 → 禁用任务 + 创建 ActionItem
```

**不可改的守卫**:

- task session 的 channel 必须是 `"task"`（不是 tg/webui）
- 跑者不绑定 TG 回调 — 发送由 agent 的 send_message 工具通过 dispatcher 完成
- 连续失败超阈值必须禁用任务（防止 API key 被无效任务烧光）
- 跑者运行在独立 event loop（`TaskScheduler`），不与 FastAPI 共享

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

- verify-admin **不能走 dispatcher**（此时尚无 Contact 行，uid→im_id 映射不存在）
- 验证码必须先存后发，发送失败回滚删除
- 验证码一次性使用：任何校验路径（成功/不匹配/过期）都必须 state_delete
- save_admin 是唯一写入 admin Contact 的地方，必须幂等

## 8. 登录与 Cookie 身份

**入口**: `magi/channels/api/auth.py`

### 两步骤登录
```
1. POST /auth/send-login-code { uid }
   └─ 通过 dispatcher 向 uid 绑定的通道发送 6 位码
   └─ 5 分钟 TTL / 60s 冷却

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

## 11. MCP 工具加载

**入口**: `magi/tools/mcp_loader.py` + `loop.py::maybe_reload_mcp_tools()`

```
启动时:
  bootstrap_mcp_tools()
    → 读 mcp_servers 表 (仅 enabled=True)
    → 每个 server 启动子进程
    → 加载 tools → 注册到 tool registry
    → 缓存 updated_at 时间戳

运行时 (每轮对话):
  maybe_reload_mcp_tools()
    → SELECT MAX(updated_at) FROM mcp_servers
    → 变化则重新 bootstrap
    → 无变化则一次廉价查询，不重建
```

**不可改的守卫**:

- 仅加载 `enabled=True` 的 server
- 运行时重载失败不崩溃 — 保留现有缓存
- MCP 工具通过 registry 统一注册，不在 loop 中特殊处理

## 12. 压缩 (Compaction)

**入口**: `magi/agent/compaction.py::maybe_compact()`

```
触发条件: estimate_messages_tokens(messages) > context_window * threshold_pct%
  └─ 配置项: Settings → Agent 设置 → 压缩阈值

压缩流程:
  1. 调用 LLM 生成旧消息摘要 (compaction prompt)
  2. 归档旧消息: UPDATE chat_messages SET archived=1
  3. 在 messages[0] 插入 "[Prior conversation summary] 摘要"
  4. 保留最近 K 条消息为活跃 (active_tail_count)
  5. 失败 → 吞掉，本轮不压缩（不阻塞对话）

FTS5 搜索:
  └─ 搜索活跃消息 (默认) + 可选 include_archived=true
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
