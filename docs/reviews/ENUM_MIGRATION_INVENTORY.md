---
title: BUS Enum 约定与迁移清单
description: BUS 内所有 enum 的统一约定（StrEnum + SAEnum）、已迁移清单、剩余候选。
permalink: /insights/enum-migration-inventory/
---

# BUS Enum 约定与迁移清单

> **本文件是 BUS 内 enum 的统一规范 + 迁移盘点**。2026-08-12 把全部
> 13 个 enum 类统一到 **`StrEnum + native SAEnum`** 一种模式 —
> 详见下方「当前约定」。本文档剩余部分记录哪些字段已迁、按什么顺序迁。

## 何时用 `StrEnum`

不是所有 `str` 字段都该 enum 化。满足**全部**下列条件才适合：

1. **封闭集合** — 有效值是 2–10 个明确的字符串，扩展需要改 schema。
2. **多文件使用** — 写入或校验发生在 ≥2 个模块（typo 风险真实存在）。
3. **discriminator 语义** — 字段被用在 ORM `WHERE`、分支判断、LLM 工具的 `input_schema.enum`、Pydantic `Literal[...]` 之类的"分流"位置。
4. **不是用户自由文本** — `body` / `subject` / `description` / `error_message` / 任意 `value` 配置项**不**该 enum 化。

## 当前约定（2026-08-12 起强制）

**所有 BUS enum 一律 `StrEnum` + native `SAEnum` 列**。不再有"loose at DB, strict at Book"的中间态。

```python
from enum import StrEnum
from sqlalchemy import Enum as SAEnum

class FooStatus(StrEnum):
    """Closed set of values for ``Foo.status``."""
    PENDING = "pending"
    DONE = "done"

class _FooRow(Base):
    status: Mapped[FooStatus] = mapped_column(
        SAEnum(
            FooStatus,
            name="foostatus",            # PG type / SQLite CHECK label
            native_enum=True,            # PG 走原生 ENUM;SQLite 走 CHECK
            length=24,
            create_constraint=True,     # SQLite 没有原生 ENUM 必须有 CHECK
            values_callable=lambda e: [m.value for m in e],  # 存 .value("pending")而非 .name("PENDING")
        ),
        nullable=False,
        default=FooStatus.PENDING,
    )
```

### 为什么是这一种

| 候选 | 选择 / 不选 | 理由 |
|------|------------|------|
| `StrEnum` + `String(N)` 列 | ❌ 不用 | DB 无约束，typo 静默写入；每加 enum 列都要写 `_coerce_*` Book boilerplate |
| `StrEnum` + native `SAEnum` 列 | ✅ **唯一选项** | DB 强约束 + Python 端 `Member == "value"` 真值成立，零业务代码改动 |
| 裸 `Enum` + native `SAEnum` 列 | ❌ 不用 | `"pending" == JobStatus.PENDING` 是 False，所有传 raw string 的代码点全破；选 `StrEnum` 反而多了 str 兼容性 |

### 为什么 `values_callable` 必须有

`StrEnum` 同时拥有 `.name`（`"PENDING"`）和 `.value`（`"pending"`）。SQLAlchemy 默认按 `.name` 存——历史 VARCHAR 行存的是 `.value`，存 `.name` 等于把所有现存数据静默改名。`values_callable=lambda e: [m.value for m in e]` 把存储锁到 `.value`，历史数据零迁移。

### Book 层不需要 `_coerce_*`

以前 `String(N)` 列需要 `Book._coerce_source(val) → TaskSource(val)` 这种样板，现在 SAEnum 列 SQLAlchemy 自动 `EnumCls(raw_str)` 完成转换，`BaseBook._row_to_dto` 默认实现就够用。

## 已完成（13/13 BUS enum，2026-08-12 全量收敛）

| Enum | 位置 | 列 / 字段 |
|------|------|-----------|
| `JobStatus` | [`magi/bus/guild/base.py`](../../magi/bus/guild/base.py) | `BaseJobRowMixin.status`（所有 `_XxxJobRow` 共用） |
| `A2AErrorCode` | [`magi/bus/guild/a2aJob.py`](../../magi/bus/guild/a2aJob.py) | `_A2ARequestRow.error_code` / `_A2ANotifyRow.error_code` |
| `MCPKind` | [`magi/bus/guild/changeMCPServerJob.py`](../../magi/bus/guild/changeMCPServerJob.py) | `_ChangeMCPServerRow.kind` |
| `ChannelEnum` | [`magi/bus/library/local/tasksBook.py`](../../magi/bus/library/local/tasksBook.py) | `_TaskRow.target_channel`（列名 `"channel"`） |
| `TaskSource` | [`magi/bus/library/local/tasksBook.py`](../../magi/bus/library/local/tasksBook.py) | `_TaskRow.source` |
| `TaskRunStatus` | [`magi/bus/library/local/tasksBook.py`](../../magi/bus/library/local/tasksBook.py) | `_TaskRow.last_status` / `_TaskRunRow.status` |
| `NoteKind` | [`magi/bus/library/local/contactBook.py`](../../magi/bus/library/local/contactBook.py) | `_ContactNoteRow.kind` |
| `Role`（`Contact.role`） | [`magi/bus/library/local/contactBook.py`](../../magi/bus/library/local/contactBook.py) | `_ContactRow.role` |
| `MemoryKind` | [`magi/bus/library/local/memoryBook.py`](../../magi/bus/library/local/memoryBook.py) | `_MemoryRow.kind` |
| `ActionSource` | [`magi/bus/library/local/actionItemBook.py`](../../magi/bus/library/local/actionItemBook.py) | `_ActionItemRow.source` |
| `ActionPriority` | [`magi/bus/library/local/actionItemBook.py`](../../magi/bus/library/local/actionItemBook.py) | `_ActionItemRow.priority` |
| `RuntimeDesiredState` | [`magi/bus/library/magis/runtimeBook.py`](../../magi/bus/library/magis/runtimeBook.py) | `_RuntimeRow.desired_state` |
| `RuntimeObservedState` | [`magi/bus/library/magis/runtimeBook.py`](../../magi/bus/library/magis/runtimeBook.py) | `_RuntimeRow.observed_state` |

## 候选清单（按推荐顺序）

### Tier 1：强烈推荐

#### 1. `MCPConnectionType` — `McpServer.connection_type`

- **位置**：[`magi/bus/library/local/mcpServerBook.py:88`](../../magi/bus/library/local/mcpServerBook.py#L88)（ORM 列）
- **值**：`"stdio"` / `"sse"` / `"streamable_http"`
- **消费方**：
  - [`mcpServerBook.py:378`](../../magi/bus/library/local/mcpServerBook.py#L378) — `upsert()` 已**内联校验**：`if connection_type not in ("stdio", "sse", "streamable_http")`
  - [`tools/mcp/add_mcp_server.py:74`](../../magi/tools/mcp/add_mcp_server.py#L74) — 工具层再次校验
  - [`tools/mcp/update_mcp_server.py:155`](../../magi/tools/mcp/update_mcp_server.py#L155) — 同上
- **当前校验**：**已存在**（Book + 两个工具各校验一次，重复）
- **建议名**：`MCPConnectionType`（成员：`STDIO` / `SSE` / `STREAMABLE_HTTP`）

#### 2. `HookSignoffStatus` — `HookSignoff.status`

- **位置**：[`magi/bus/library/local/hookSignoffBook.py:54`](../../magi/bus/library/local/hookSignoffBook.py#L54)（ORM 列）
- **值**：`"pending"` / `"done"` / `"failed"`
- **消费方**：
  - [`hookSignoffBook.py:82`](../../magi/bus/library/local/hookSignoffBook.py#L82) — `list_pending()` 过滤 `"pending"`
  - [`bus/guild/base.py:170, 257, 380`](../../magi/bus/guild/base.py#L170) — job board 通用扫描
- **当前校验**：**没有**
- **建议名**：`HookSignoffStatus`（成员：`PENDING` / `DONE` / `FAILED`）

#### 3. `ToolSource` — `ToolDefinition.source`

- **位置**：[`magi/bus/library/local/toolsBook.py:99`](../../magi/bus/library/local/toolsBook.py#L99)（ORM 列，`default="manual"`）
- **值**：`"builtin"` / `"mcp"` / `"manual"`
- **消费方**：
  - [`tools/worker.py:96, 130`](../../magi/tools/worker.py#L96) — 读 `definition.source`
  - [`tools/registry.py:62, 170`](../../magi/tools/registry.py#L62) — registry 用作稳定标识
  - [`channels/api/tools.py:33, 59, 107, 130`](../../magi/channels/api/tools.py#L33) — Pydantic 注释用 `Literal["builtin", "mcp"]`，按 `"mcp"` 过滤
  - [`bus/guild/runToolJob.py:130`](../../magi/bus/guild/runToolJob.py#L130) — 透传
- **当前校验**：**完全没有**——Tier 1 中**唯一**目前没有验证面的，这是真正的缺口
- **建议名**：`ToolSource`（成员：`BUILTIN` / `MCP` / `MANUAL`）

### Tier 2：值得做但更大

#### 4. `ShellStatus` — background shell status

- **位置**：[`magi/tools/shell/_manager.py:90`](../../magi/tools/shell/_manager.py#L90)
- **值**：`"running"` / `"completed"` / `"failed"` / `"terminated"` / `"error"`
- **当前校验**：仅通过命名方法（`update_status` / `mark_error` / `terminate`）设置；`_TERMINAL_STATUSES` frozenset 用在 reap
- **风险**：**极低**（纯内存，无持久化）
- **建议名**：`ShellStatus`

### Tier 3：概念上需要先讨论

#### 5. `ConversationChannel` vs `ChannelEnum` 复用

- **位置**：[`magi/bus/library/local/conversationBook.py:243`](../../magi/bus/library/local/conversationBook.py#L243)（ORM 列）
- **当前值**：`"tg"` / `"webui"` / `"task"` + 隐式 `"a2a"`（来自 A2A 路径）
- **冲突点**：`tasksBook.py` 的 `ChannelEnum` 也是 `TG` / `WEBUI` / `A2A` / `SCHEDULED`——表面值一样，但**语义不同**：
  - `Conversation.channel` = "这条对话是从哪个**入口**来的"
  - `ChannelEnum`（Task） = "任务**投递到**哪个 channel"
- **建议**：先和 reviewer 讨论两个概念是否要合并。结论"合并"就 enum 化 + 引用同一个 enum；结论"分开"就独立 `ConversationChannel`。

#### 6. `AgentMessageRole`（diff 面太大）

- **位置**：[`magi/bus/library/local/conversationBook.py:61, 165`](../../magi/bus/library/local/conversationBook.py#L61)
- **值**：`"user"` / `"assistant"` / `"system"` / `"tool"`
- **使用广度**：仓库里到处都是——providers、agent worker、compaction、job board、agent_context
- **风险**：技术安全（`StrEnum` 是 `str` 子类，所有 `== "user"` 不变），但**改动面太大**，建议单独 PR
- **建议名**：`AgentMessageRole`（注意：和 Pydantic 消息协议的角色名一致；OpenAI / Anthropic SDK 都用 `"user"` / `"assistant"` 等小写，所以 `Member.value` 就是协议字符串，零转换）

## 不推荐 enum 化（误报陷阱）

| 字段 | 位置 | 不做的理由 |
|------|------|-----------|
| `_CONTACT_ROLES` (`("admin", "assigned", "guest", "contact")`) | [`magi/channels/api/contacts.py:40`](../../magi/channels/api/contacts.py#L40) | 包含 `admin`（MAGIS 概念，外部依赖），跟 `Contact.role`（本地 `assigned`/`guest`）正交。等 MAGIS 那边的 `Role` 概念定型后再说 |
| `ALLOWED_ROLES = frozenset({"admin", "assigned"})` | 多个 tool | 同上，工具 gate 的 `admin` 是 MAGIS 派生角色，不是本地 contact 角色 |
| 各种 `body` / `subject` / `description` / `error_message` / setting `value` | 多处 | 用户自由文本，封闭集合不成立 |
| `ChangeMCPServerJob` 之外的 job 字段（`name` / `args` / `kwargs`） | 多处 | `args` 显然是 free-form 数据 |

## 推荐执行顺序（针对剩余候选）

1. **一个 PR 一个 enum**，不要 batch。理由：每个都是 Book/dataclass + 调用方 + 测试，单独 PR 评审干净、revert 容易。
2. **PR 顺序**（先做风险最低、收益最高的）：
   1. `MCPConnectionType`（已有校验，机械替换）
   2. `HookSignoffStatus`（补缺口）
   3. `ToolSource`（补缺口；**唯一**完全没校验面的）
3. **Tier 2** 等 Tier 1 都稳定后再排期。
4. **Tier 3** 单独讨论完概念再决定。

## 模板（沿用 BUS 已完成 enum 的标准形态）

每个新 PR 至少包含：

1. 引入 `class Xxx(StrEnum)`（带 docstring 解释值语义 + 引用本文档「当前约定」段）
2. ORM 列改 `SAEnum(Xxx, name="xxx", native_enum=True, length=24, create_constraint=True, values_callable=lambda e: [m.value for m in e])`，类型注解从 `Mapped[str]` 改成 `Mapped[Xxx]`
3. DTO 字段类型 `str` → `Xxx`
4. 删 `Book._coerce_*`（SAEnum 自动 coerce）；`_row_to_dto` 退回基类默认实现
5. Book `add()` / `upsert()` 已有 `if x not in MyEnum` 校验即可（`StrEnum` 是 `str` 子类，enum 成员 + raw string 都通过）
6. `__all__` + 包级 `__init__.py` 重新导出
7. 加一个 `test_*_rejects_unknown_*` 锁定新校验面（DB CHECK 抛 IntegrityError）
8. 现有测试里如果硬编码了 `kind="..."` 字符串，**不要动**——`StrEnum == "value"` 恒为 `True`，保留原样是回归保护

## 历史：被淘汰的设计

### ❌ "loose at DB, strict at Book" 模式（已删）

旧模式：列保持 `String(N)`、Book 层 `_coerce_source(val)` 把 raw string 归一化到 enum。
缺点：每加一个 enum 列都要写一遍 coerce 方法 + `_row_to_dto` 分支；DB 无校验，typo 静默。
代码考古：曾在 [`magi/bus/library/local/tasksBook.py`](../../magi/bus/library/local/tasksBook.py) 留有 `_coerce_source` / `_coerce_last_status` / `_coerce_status` 三个样板，2026-08-12 全部删除。

### ❌ `RunTaskJob.fired_by` closed set（已删）

`docs/business-flows.md` 曾写过 `fired_by ∈ {cron_tick, run_at_consume, api_manual_run, schedule_task_tool}` 这个 closed set 约定。但 `fired_by` 字段从来没在 `RunTaskJob` 上实现过——只是 doc 写了；现在 `RunTaskJob` 已经简化到只剩 `task_id` + `manual`，这层抽象整个去掉。