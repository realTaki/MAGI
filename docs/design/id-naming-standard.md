# ID 命名规范

> 状态：方案 / 待实施
> 日期：2026-08-09

## 总览

| # | 概念 | 当前 | → | 规范 |
|---|------|------|---|------|
| 1 | MAGI 实例 | `magi_id` / `magic_id` | → | **`magi_id`** |
| 2 | MAGIS 树 | `magis_id` | → | `magis_id` ✅ 不变 |
| 3 | 用户/联系人 | `uid` / `contact_id` | → | **`contact_id`** |
| 4 | Telegram 用户 | `telegram_id` / `tg_id` / `tgid` | → | **`tgid`** |
| 5 | Telegram 聊天 | `chat_id` / `tg_chat_id` | → | `chat_id` ✅ 不变（Telegram channel 内可与 tgid 混用） |
| 6 | 对话 | `session_id` | → | **`conversation_id`** |
| 7 | 消息 | `message_id` | → | `message_id` ✅ 不变 |
| 8 | 对话（简写） | `conv_id` | → | **`conversation_id`**（与 #6 统一） |
| 9 | 事件/Job | `event_id` | → | **`job_id`** |
| 10 | Agent Run | `run_id` | → | **移除**（Agent 已无 run 设计，steering 用 conversation_id） |
| 11+ | job_id, tool_call_id, task_id, memory_id, note_id, ... | — | → | ✅ 不变 |

---

## 详细变更清单

### 1. `magic_id` → `magi_id`

**理由**：拼写错误残留，与 `magi_id` 指同一概念（MAGI 实例 ID）。

| 文件 | 次数 | 说明 |
|------|------|------|
| `magi/bus/library/magis/evaRuntimeBook.py` | 1 | ORM 注释 |
| `magi/channels/api/runtime_access.py` | 11 | 运行时访问控制 |
| `magi/channels/api/auth.py` | 1 | 认证 |

**影响**：低。仅重命名，无 DB 迁移。

---

### 2. `uid` → `contact_id`

**理由**：`uid` 语义模糊（user? unique?），且 DB 层和 API 层命名不一致。统一为 `contact_id`，与 `contacts` 表、`ContactBook` 对齐。

**⚠️ 高风险**：`uid` 是多个 ORM 表的列名，需要 DB 迁移。

**受影响范围（按出现次数降序）**：

| 文件 | uid 出现次数 | 说明 |
|------|-------------|------|
| `magi/channels/api/auth.py` | 133 | 认证层大量使用 |
| `magi/bus/library/local/sessionBook.py` | 36 | `uid` 列 + FK |
| `magi/channels/api/chat_sessions.py` | 35 | 会话 API |
| `magi/bus/library/local/tasksBook.py` | 35 | `uid` 列 + FK |
| `magi/channels/api/chat.py` | 22 | 聊天 API |
| `magi/channels/api/onboarding.py` | 16 | 入职 API |
| `magi/channels/api/password_utils.py` | 15 | 密码工具 |
| `magi/channels/api/tasks.py` | 13 | 任务 API |
| `magi/channels/a2a/adapter.py` | 13 | A2A 适配器 |
| `magi/bus/library/magis/magisBook.py` | 13 | MAGIS |
| `magi/agent/worker.py` | 13 | Agent Worker |
| `magi/tools/memory/contacts/add_contact_note.py` | 12 | 工具 |
| `magi/channels/api/auth_gates.py` | 12 | 认证门 |
| `magi/channels/api/token_metrics.py` | 11 | Token 统计 |
| `magi/channels/api/action_items.py` | 11 | Action Items |
| `magi/tools/memory/sessions/search_sessions.py` | 10 | 工具 |
| `magi/bus/library/magis/authCredentialBook.py` | 10 | 认证凭证 |
| *其余 20+ 文件* | ≤8 次/文件 | — |

**涉及 ORM 表（需要 `ALTER TABLE`）**：

| 表 | 列 |
|----|-----|
| `chat_sessions` | `uid` → `contact_id` |
| `chat_messages` | `uid` → `contact_id` |
| `tasks` | `uid` → `contact_id` |
| `memory_entries` | `uid` → `contact_id` |
| `token_usage` | `uid` → `contact_id` |
| `action_items` | `uid` → `contact_id` |
| `hook_signoffs` | `uid` → `contact_id` |

---

### 3. `telegram_id` / `tg_id` → `tgid`

**理由**：统一为简洁的 `tgid`。同时更新 ORM 列名。

| 文件 | 当前命名 | 次数 |
|------|---------|------|
| `magi/channels/api/tg_bindings.py` | `telegram_id` | 34 |
| `magi/channels/api/auth.py` | `telegram_id` | 37 |
| `magi/channels/api/runtime_access.py` | `telegram_id` | 21 |
| `magi/channels/api/contacts.py` | `telegram_id` | 20 |
| `magi/channels/telegram/worker.py` | `telegram_id` | 大量 |
| `magi/bus/library/local/contactBook.py` | `telegram_id` + `tg_id` | 23 + 6 |
| `magi/channels/api/onboarding.py` | `telegram_id` + `tg_id` | 大量 |
| `magi/channels/api/chat.py` | `telegram_id` | 若干 |
| `magi/channels/api/chat_sessions.py` | `telegram_id` | 若干 |
| `magi/channels/api/tasks.py` | `telegram_id` | 2 |
| `magi/channels/api/runtime_control.py` | `telegram_id` | 2 |
| `magi/channels/api/runtime_proxy.py` | `telegram_id` | 若干 |
| `magi/channels/api/proxy_auth.py` | `telegram_id` | 若干 |
| `magi/channels/api/control_runtime.py` | `telegram_id` | 若干 |
| `magi/tools/tasks/schedule.py` | `telegram_id` | 若干 |
| `magi/tools/memory/contacts/add_contact.py` | `telegram_id` | 若干 |

**涉及 ORM 列**：`contacts.telegram_id` → `contacts.tgid`

---

### 4. `tg_chat_id` → `chat_id`

**理由**：在 Telegram channel 内 `chat_id` 已足够明确，私聊时 `tgid == chat_id`。

| 文件 | 次数 |
|------|------|
| `magi/bus/guild/chatJob.py` | 1 |
| `magi/channels/telegram/worker.py` | 1 |

**影响**：极低。

---

### 5. `session_id` → `conversation_id`（含 DB 表 `chat_sessions`）

**理由**：`session` 容易与数据库 session（SQLAlchemy）混淆。统一为 `conversation_id`。

**⚠️ 高风险**：涉及 ORM 表 `chat_sessions` 重命名 → `chat_conversations`，列 `session_id` → `conversation_id`，以及所有外键引用。

**受影响文件（20+）**：

| 文件 | 次数 | 说明 |
|------|------|------|
| `magi/bus/library/local/sessionBook.py` | 57 | ORM 定义 + Book 方法 |
| `magi/channels/api/chat_sessions.py` | 48 | 会话 API |
| `magi/channels/api/chat.py` | 25 | 聊天 API |
| `magi/agent/worker.py` | 19 | Agent Worker |
| `magi/bus/library/local/tasksBook.py` | 14 | 任务 Book（FK `session_id`） |
| `magi/bus/guild/chatJob.py` | 若干 | ChatJob |
| `magi/bus/guild/callLLMJob.py` | 1 | LLM Job |
| `magi/bus/guild/runTaskJob.py` | 若干 | Task Job |
| `magi/tools/comms/send_message.py` | 10 | 工具 |
| `magi/tools/memory/sessions/search_sessions.py` | 若干 | 工具 |
| `magi/agent/compaction.py` | 若干 | Compaction |
| `magi/agent/auto_title.py` | 若干 | Auto title |
| `magi/agent/agent_context.py` | 若干 | Agent context |
| `magi/channels/telegram/worker.py` | 若干 | Telegram Worker |
| `magi/channels/webui/worker.py` | 若干 | WebUI Worker |
| `magi/channels/tasks/worker.py` | 若干 | Tasks Worker |
| `magi/channels/api/tasks.py` | 若干 | 任务 API |
| `magi/tools/tasks/schedule.py` | 若干 | 工具 |
| `magi/tools/base.py` | 1 | 工具基类 |
| `magi/tools/worker.py` | 若干 | 工具 Worker |

**涉及 ORM 表/列**：

| 变更 | 说明 |
|------|------|
| 表 `chat_sessions` → `chat_conversations` | 主表重命名 |
| `chat_sessions.session_id` → `chat_conversations.conversation_id` | 主键 |
| `chat_messages.session_id` → `chat_messages.conversation_id` | 外键 |
| `tasks.session_id` → `tasks.conversation_id` | 外键 (SET NULL) |
| `task_runs.session_id` → `task_runs.conversation_id` | 外键 (SET NULL) |
| `sessionBook.py` → `conversationBook.py` | 文件重命名 |

---

### 6. `conv_id` → `conversation_id`

**理由**：与 #5 统一，消除简写。

| 文件 | 次数 |
|------|------|
| `magi/agent/worker.py` | 12 |

**影响**：低。仅一个文件，且 `conversation_id` 已在同文件使用。

---

### 7. `event_id` → `job_id`

**理由**：chat event 本质上是一个 job（chatJobBoard 上的 ChatJob）。统一为 `job_id`。

| 文件 | event_id 次数 | 说明 |
|------|-------------|------|
| `magi/agent/worker.py` | 15 | Agent Worker |
| `magi/bus/guild/chatJob.py` | 18 | ChatJob 定义 + publish_chat |
| `magi/bus/guild/base.py` | 1 | 基类 |
| `magi/channels/telegram/worker.py` | 1 | Telegram Worker |
| `magi/channels/a2a/router.py` | 14 | A2A Router |
| `magi/channels/a2a/transport.py` | 若干 | A2A Transport |
| `magi/channels/api/chat.py` | 1 | 聊天 API |

**涉及 ORM**：`chat_jobs` 表 `event_id` 列 → `job_id`

**注意**：现有的 `job_id`（用于 tool_jobs、llm_jobs 等）保持不变，二者语义一致——都是各自 job board 上的 job 标识。

---

### 8. `run_id`（Agent 上下文）→ 移除，改用 `conversation_id`

**理由**：Agent 已无 "run" 设计，steering 通过 `conversation_id` 判断。Agent 上下文的 `run_id` 应移除。

**Agent 相关（应移除/替换）**：

| 文件 | 当前用法 | 操作 |
|------|---------|------|
| `magi/agent/worker.py` | `run_id=getattr(message, "target_run_id", None) or f"turn-{uuid.uuid4().hex}"` (line 593) | 替换为 `conversation_id` |
| `magi/agent/worker.py` | 159/188 行读取/返回 `run_id` | 移除 |
| `magi/bus/guild/chatJob.py` | `ChatJob.run_id` 字段（line 32） | 移除列 |
| `magi/bus/guild/chatJob.py` | `publish_chat(run_id=...)` 参数（line 248） | 移除参数 |
| `magi/channels/api/runs.py` | `/runs/{run_id}` API 端点 | 移除或重写为 conversation 端点 |

**⚠️ 待确认**：以下文件中的 `run_id` 与 Agent run 无关，可能保留：

| 文件 | 上下文 | 建议 |
|------|--------|------|
| `magi/bus/library/local/tasksBook.py` | Task run 记录 (`task_runs` 表) | 保留（Task 概念不同于 Agent） |
| `magi/bus/guild/runTaskJob.py` | `RunTaskJob.run_id` | 保留（Task job 运行标识） |
| `magi/bus/guild/runToolJob.py` | `RunToolJob.run_id` | 保留（Tool job 运行标识） |
| `magi/bus/library/local/tokenUsageBook.py` | Token 使用按 run 统计 | 可能需要改为 conversation_id |
| `magi/bus/guild/deliveryJob.py` | Delivery 关联 run | 待评估 |
| `magi/bus/guild/sendA2AJob.py` | A2A 关联 run | 待评估 |

---

## 不变（确认无需改动）

| 概念 | 命名 | 理由 |
|------|------|------|
| MAGIS 树 | `magis_id` | 无歧义 |
| 消息 | `message_id` | 无歧义 |
| Job | `job_id` | 无歧义，与 #7（event_id → job_id）语义一致 |
| Tool call | `tool_call_id` | `tc_id`（agent/worker.py 内部）可保留为局部简写 |
| Task | `task_id` | 无歧义 |
| Memory 条目 | `memory_id` | 无歧义 |
| Contact note | `note_id` | 无歧义 |
| Action item | `action_item_id` | `item_id` 简写可保留在局部 |
| Runtime | `runtime_id` | 无歧义 |
| MAGIS 角色 | `role_id` | 无歧义 |
| 父 MAGIS | `parent_id` | 无歧义 |
| Adam MAGI | `adam_id` | 无歧义 |
| Shell 会话 | `bash_id` | 无歧义 |
| Connector 实例 | `instance_id` | 无歧义 |
| Plugin | `plugin_id` | 无歧义 |
| Hook signoff | `signoff_id` | 无歧义 |

---

## 实施建议

### 分阶段执行

**Phase 1 — 低风险（仅代码重命名，无 DB 迁移）**
- #1 `magic_id` → `magi_id`
- #4 `tg_chat_id` → `chat_id`
- #6 `conv_id` → `conversation_id`

**Phase 2 — 中风险（涉及 DB 列重命名 + 迁移）**
- #3 `telegram_id` / `tg_id` → `tgid`（`contacts` 表）
- #7 `event_id` → `job_id`（`chat_jobs` 表）
- #8 `run_id` 移除（`chat_jobs` 表）

**Phase 3 — 高风险（涉及多张表 + 所有引用）**
- #2 `uid` → `contact_id`（7 张表）
- #5 `session_id` → `conversation_id`（3 张表 + 文件重命名）
