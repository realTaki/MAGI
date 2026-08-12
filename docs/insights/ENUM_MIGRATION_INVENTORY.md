---
title: String → Enum 改造清单
description: 仓库中还在用字符串、但实际是封闭离散集合的字段；按"该不该做"排好优先级。
permalink: /insights/enum-migration-inventory/
---

# String → Enum 改造清单

> **本文件是 enum 化候选的盘点**。背景：2026-08-12 把 `ContactNote.kind`（→ `NoteKind`）、
> `Memory.kind`（→ `MemoryKind`）、`Contact.role`（→ `Role`）从字符串常量/裸字符串迁到
> `enum.StrEnum`，统一了三个核心 Book 的 discriminator 写法。这份文档把同类工作梳理清楚，
> 按"性价比"排好顺序。

## 何时用 `StrEnum`

不是所有 `str` 字段都该 enum 化。满足**全部**下列条件才适合：

1. **封闭集合** — 有效值是 2–10 个明确的字符串，扩展需要改 schema。
2. **多文件使用** — 写入或校验发生在 ≥2 个模块（typo 风险真实存在）。
3. **discriminator 语义** — 字段被用在 ORM `WHERE`、分支判断、LLM 工具的 `input_schema.enum`、Pydantic `Literal[...]` 之类的"分流"位置。
4. **不是用户自由文本** — `body` / `subject` / `description` / `error_message` / 任意 `value` 配置项**不**该 enum 化。

## 约定（已形成的 codebase 惯例）

- 用 `enum.StrEnum`（不是裸 `Enum`），成员继承 `str`，`Member == "value"` 恒为 `True`。
- 类名用 **业务名词**：`NoteKind`、`MemoryKind`、`ActionSource`、`ChannelEnum`、`Role`。
- 成员名用 **领域正名**：`PERMANENT` / `DAILY` / `FACT` / `QUICK_NOTE` / `ASSIGNED` / `GUEST`。
- 不强行加 `ALL_*` frozenset——如果用 `if x not in MyEnum:` 就够了（参考
  [`magi/bus/library/local/memoryBook.py`](../../magi/bus/library/local/memoryBook.py) 的 `MemoryKind`），
  没有强制约束的 `frozenset` 反而是冗余。要加就保持 `frozenset[str]` 形式（参考 `ALL_NOTE_KINDS`、
  `ALL_SOURCES`）。
- ORM 列类型 **保持 `String(16)` / `String(32)`**，不要切到 `SAEnum`（避免 schema migration；`StrEnum`
  是 `str` 子类，列里存的就是 `.value`，零兼容成本）。
- DTO 字段类型从 `str` 换成 enum；`_row_to_dto` 不做转换（`StrEnum` 是 `str`，read 出来是 `str`，
  Python 接受 `str` 赋给 `NoteKind` 类型槽位——和 `actionItemBook` 现状一致）。

## 已完成（不要重复做）

| 字段 | 类型 | 位置 |
|------|------|------|
| `NoteKind` | `PERMANENT` / `DAILY` | [`magi/bus/library/local/contactBook.py`](../../magi/bus/library/local/contactBook.py) |
| `MemoryKind` | `FACT` / `QUICK_NOTE` | [`magi/bus/library/local/memoryBook.py`](../../magi/bus/library/local/memoryBook.py) |
| `Role`（`Contact.role`） | `ASSIGNED` / `GUEST` | [`magi/bus/library/local/contactBook.py`](../../magi/bus/library/local/contactBook.py) |
| `ActionSource` | `USER` / `PROACTIVE` | [`magi/bus/library/local/actionItemBook.py`](../../magi/bus/library/local/actionItemBook.py) |
| `ActionPriority` | `NORMAL` / `HIGH` | 同上 |
| `ChannelEnum` | `TG` / `WEBUI` / `A2A` / `SCHEDULED` | [`magi/bus/library/local/tasksBook.py`](../../magi/bus/library/local/tasksBook.py) |
| `RuntimeDesiredState` / `RuntimeObservedState` | 6 个值 | [`magi/bus/library/magis/runtimeBook.py`](../../magi/bus/library/magis/runtimeBook.py) |
| `MCPTimeout` | 枚举秒数 | [`magi/bus/library/local/settingBook.py`](../../magi/bus/library/local/settingBook.py) |
| `ConnectorEventKind` | `DATA` / `CREATED` / `UPDATED` / ... | [`magi/connectors/base.py`](../../magi/connectors/base.py) |

## 候选清单（按推荐顺序）

### Tier 1：强烈推荐

> 封闭集合 + 多文件 + 已有（或缺）校验面 + 改动小。零 DB 迁移，零 wire-format 变化。

#### 1. `MCPConnectionType` — `McpServer.connection_type`

- **位置**：[`magi/bus/library/local/mcpServerBook.py:88`](../../magi/bus/library/local/mcpServerBook.py#L88)（ORM 列）
- **值**：`"stdio"` / `"sse"` / `"streamable_http"`
- **消费方**：
  - [`mcpServerBook.py:378`](../../magi/bus/library/local/mcpServerBook.py#L378) — `upsert()` 已**内联校验**：
    `if connection_type not in ("stdio", "sse", "streamable_http")`
  - [`tools/mcp/add_mcp_server.py:74`](../../magi/tools/mcp/add_mcp_server.py#L74) — 工具层再次校验同一组字符串
  - [`tools/mcp/update_mcp_server.py:155`](../../magi/tools/mcp/update_mcp_server.py#L155) — 同上
- **当前校验**：**已存在**（Book + 两个工具各校验一次，重复）
- **风险**：**极低**。改 enum 之后三处 `if ... not in (...)` 全部归一为 `if connection_type not in MCPConnectionType`
- **建议名**：`MCPConnectionType`（成员：`STDIO` / `SSE` / `STREAMABLE_HTTP`）

#### 2. `MCPKind` — `McpServerChangedJob.kind`

- **位置**：[`magi/bus/guild/mcpServerChangedJob.py:82`](../../magi/bus/guild/mcpServerChangedJob.py#L82)
- **值**：`"added"` / `"updated"` / `"deleted"` / `"toggled"`
- **当前校验**：**已有 `VALID_KINDS: frozenset[str]`** + `__post_init__` 校验——全仓库 best-existing pattern
- **风险**：**极低**。直接用 `StrEnum` 替换 `frozenset`，单文件改动
- **建议名**：`MCPKind`（成员：`ADDED` / `UPDATED` / `DELETED` / `TOGGLED`）

#### 3. `TaskRunStatus` — `TaskRun.status`

- **位置**：[`magi/bus/library/local/tasksBook.py:284`](../../magi/bus/library/local/tasksBook.py#L284)（ORM 列）
- **值**：`"running"` / `"completed"` / `"failed"`
- **消费方**：
  - [`tasksBook.py:797`](../../magi/bus/library/local/tasksBook.py#L797) — `record_run_start()` 写 `"running"`
  - [`tasksBook.py:858`](../../magi/bus/library/local/tasksBook.py#L858) — `complete()` 写任意字符串（**没校验**）
  - [`tasksBook.py:884-889`](../../magi/bus/library/local/tasksBook.py#L884) — `reap_stale()` 过滤 `"running"`，翻成 `"failed"`
  - [`bus/guild/base.py:380`](../../magi/bus/guild/base.py#L380) — job board 通用逻辑：`status not in ("completed", "failed")`
  - [`bus/guild/a2aJob.py:219`](../../magi/bus/guild/a2aJob.py#L219) — 同上
- **当前校验**：**没有**。`complete()` 接受任意值，typo 直接写库
- **风险**：低。DB 有现存的行，但 `StrEnum` 是 `str` 子类，读写完全兼容
- **建议名**：`TaskRunStatus`（成员：`RUNNING` / `COMPLETED` / `FAILED`）

#### 4. `HookSignoffStatus` — `HookSignoff.status`

- **位置**：[`magi/bus/library/local/hookSignoffBook.py:54`](../../magi/bus/library/local/hookSignoffBook.py#L54)（ORM 列）
- **值**：`"pending"` / `"done"` / `"failed"`
- **消费方**：
  - [`hookSignoffBook.py:82`](../../magi/bus/library/local/hookSignoffBook.py#L82) — `list_pending()` 过滤 `"pending"`
  - [`bus/guild/base.py:170, 257, 380`](../../magi/bus/guild/base.py#L170) — job board 通用扫描
- **当前校验**：**没有**（但写入点都是内部 worker，typo 风险中等）
- **风险**：低
- **建议名**：`HookSignoffStatus`（成员：`PENDING` / `DONE` / `FAILED`）

#### 5. `ToolSource` — `ToolDefinition.source`

- **位置**：[`magi/bus/library/local/toolsBook.py:99`](../../magi/bus/library/local/toolsBook.py#L99)（ORM 列，`default="manual"`）
- **值**：`"builtin"` / `"mcp"` / `"manual"`
- **消费方**：
  - [`tools/worker.py:96, 130`](../../magi/tools/worker.py#L96) — 读 `definition.source`
  - [`tools/registry.py:62, 170`](../../magi/tools/registry.py#L62) — registry 用作稳定标识
  - [`channels/api/tools.py:33, 59, 107, 130`](../../magi/channels/api/tools.py#L33) — Pydantic 注释用 `Literal["builtin", "mcp"]`，按 `"mcp"` 过滤
  - [`bus/guild/runToolJob.py:130`](../../magi/bus/guild/runToolJob.py#L130) — 透传
- **当前校验**：**完全没有**。Tier 1 中**唯一**一个目前没有验证面的——这是真正的缺口
- **风险**：低（DB 有现存的行；column 保持 `String(32)`）
- **建议名**：`ToolSource`（成员：`BUILTIN` / `MCP` / `MANUAL`）

#### 6. `SeedPresetTrigger` — `SeedPresetTasksJob.trigger`

- **位置**：[`magi/bus/guild/seedPresetTasksJob.py:29`](../../magi/bus/guild/seedPresetTasksJob.py#L29)（dataclass 字段，不是 ORM）
- **值**：`"contact_created"` / `"contact_promoted"`
- **消费方**：
  - [`seedPresetTasksJob.py:29`](../../magi/bus/guild/seedPresetTasksJob.py#L29) — 字段定义
  - [`channels/api/contacts.py:274`](../../magi/channels/api/contacts.py#L274) — `trigger="contact_created"`
  - [`channels/api/contacts.py:464`](../../magi/channels/api/contacts.py#L464) — `trigger="contact_promoted"`
- **当前校验**：**没有**
- **风险**：**极低**（job board dataclass，非持久化字段）
- **建议名**：`SeedPresetTrigger`（成员：`CONTACT_CREATED` / `CONTACT_PROMOTED`）

### Tier 2：值得做但更大

#### 7. `ShellStatus` — background shell status

- **位置**：[`magi/tools/shell/_manager.py:90`](../../magi/tools/shell/_manager.py#L90)
- **值**：`"running"` / `"completed"` / `"failed"` / `"terminated"` / `"error"`
- **当前校验**：仅通过命名方法（`update_status` / `mark_error` / `terminate`）设置；`_TERMINAL_STATUSES` frozenset 用在 reap
- **风险**：**极低**（纯内存，无持久化）
- **建议名**：`ShellStatus`

#### 8. `TaskTrigger` + `RunTaskJob.fired_by`（建议合并）

- **位置**：
  - `TaskRun.trigger`：[`tasksBook.py:280`](../../magi/bus/library/local/tasksBook.py#L280)（ORM 列）
  - `RunTaskJob.fired_by`：[`runTaskJob.py:28, 55`](../../magi/bus/guild/runTaskJob.py#L28)（dataclass + ORM 列）
- **值**（4 个 docstring 写明）：`cron_tick` / `run_at_consume` / `api_manual_run` / `schedule_task_tool`
- **额外值**（SeedPreset 用）：`contact_created` / `contact_promoted`——见 #6，建议分两个 enum
- **当前校验**：**没有**。`record_run_start(trigger=trigger)` 接受任意字符串
- **风险**：中等。`TaskRun.trigger` 是持久化列；改 enum 需要更新所有写入点（[`channels/tasks/worker.py:52, 87, 103`](../../magi/channels/tasks/worker.py#L52) 等）
- **建议名**：`TaskTrigger`（任务运行触发）+ 复用 #6 的 `SeedPresetTrigger`（种子里触发）

### Tier 3：概念上需要先讨论

#### 9. `ConversationChannel` vs `ChannelEnum` 复用

- **位置**：[`magi/bus/library/local/conversationBook.py:243`](../../magi/bus/library/local/conversationBook.py#L243)（ORM 列）
- **当前值**：`"tg"` / `"webui"` / `"task"` + 隐式 `"a2a"`（来自 A2A 路径）
- **冲突点**：`tasksBook.py` 的 `ChannelEnum` 也是 `TG` / `WEBUI` / `A2A` / `SCHEDULED`——
  表面值一样，但**语义不同**：
  - `Conversation.channel` = "这条对话是从哪个**入口**来的"
  - `ChannelEnum`（TaskRun） = "任务**投递到**哪个 channel"
- **建议**：先和 reviewer 讨论两个概念是否要合并。结论"合并"就 enum 化 + 引用同一个 enum；
  结论"分开"就独立 `ConversationChannel`。

#### 10. `AgentMessageRole`（diff 面太大）

- **位置**：[`magi/bus/library/local/conversationBook.py:61, 165`](../../magi/bus/library/local/conversationBook.py#L61)
- **值**：`"user"` / `"assistant"` / `"system"` / `"tool"`
- **使用广度**：仓库里到处都是——providers、agent worker、compaction、job board、agent_context
- **风险**：技术安全（`StrEnum` 是 `str` 子类，所有 `== "user"` 不变），但**改动面太大**，建议单独 PR
- **建议名**：`AgentMessageRole`（注意：和 Pydantic 消息协议的角色名一致；OpenAI / Anthropic SDK
  都用 `"user"` / `"assistant"` 等小写，所以 `Member.value` 就是协议字符串，零转换）

## 不推荐 enum 化（误报陷阱）

| 字段 | 位置 | 不做的理由 |
|------|------|-----------|
| `_CONTACT_ROLES` (`("admin", "assigned", "guest", "contact")`) | [`magi/channels/api/contacts.py:40`](../../magi/channels/api/contacts.py#L40) | 包含 `admin`（MAGIS 概念，外部依赖），跟 `Contact.role`（本地 `assigned`/`guest`）正交。等 MAGIS 那边的 `Role` 概念定型后再说 |
| `ALLOWED_ROLES = frozenset({"admin", "assigned"})` | 多个 tool | 同上，工具 gate 的 `admin` 是 MAGIS 派生角色，不是本地 contact 角色 |
| 各种 `body` / `subject` / `description` / `error_message` / setting `value` | 多处 | 用户自由文本，封闭集合不成立 |
| `McpServerChangedJob` 之外的 job 字段（`name` / `args` / `kwargs`） | 多处 | `args` 显然是 free-form 数据 |

## 推荐执行顺序

1. **一个 PR 一个 enum**，不要 batch。理由：每个都是 Book/dataclass + 调用方 + 测试，单独 PR 评审干净、revert 容易。
2. **PR 顺序**（先做风险最低、收益最高的）：
   1. `MCPConnectionType`（已有校验，机械替换）
   2. `MCPKind`（升级已有 `VALID_KINDS`）
   3. `TaskRunStatus`（补缺口）
   4. `HookSignoffStatus`（补缺口）
   5. `ToolSource`（补缺口；**唯一**完全没校验面的）
   6. `SeedPresetTrigger`（最小改动，纯 dataclass）
3. **Tier 2** 等 Tier 1 都稳定后再排期。
4. **Tier 3** 单独讨论完概念再决定。

## 模板

每个 PR 至少包含：

1. 引入 `class Xxx(StrEnum)`（带 docstring 解释值语义 + 引用此文档）
2. ORM 列 `default=` 同步换成 enum 成员；列类型保持 `String(N)`
3. DTO 字段类型 `str` → enum
4. Book `add()` / `upsert()` 加 `if x not in Xxx` 校验（如已有内联校验，归一化）
5. 所有写入点改成 enum 成员；读取点保持字符串字面量（`StrEnum` 比较兼容）
6. `__all__` + 包级 `__init__.py` 重新导出
7. 加一个 `test_*_rejects_unknown_*` 锁定新校验面
8. 现有测试里如果硬编码了 `kind="..."` 字符串（注意 `StrEnum == "value"` 恒为 `True`），**不要动**——保留原样是回归保护

参考实现：[`60b729b`](https://github.com/...)（`refactor(library): 将 ContactNote.kind 改为强类型 NoteKind 枚举并新增 ContactBook.touch`）。
