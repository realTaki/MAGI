---
title: Book 层 schema 统一与去重方案（Review）
description: 盘点 magi/bus/library 各 Book 的 dataclass 与 Row 字段重复、时间戳与 ID 格式不统一的历史债，提出"放弃向后兼容、统一重写"的收敛方向与待决策项。
permalink: /insights/book-schema-unification/
---

# Book 层 schema 统一与去重方案（Review）

> **状态：提案 / 待决策**（2026-08-15）
>
> **结论方向**：放弃向后兼容，统一时间戳与 ID 格式，消除
> dataclass/Row 之间的**横向重复**与跨表之间的**纵向重复**。

## 1. 问题陈述

`magi/bus/library` 下 21 个 Book，每个文件都遵循同一套三件式结构：

```text
@dataclass(frozen=True, slots=True)   # 公开 DTO
class Xxx: ...

class _XxxRow(Base):                  # 内部 ORM
    __tablename__ = "..."
    ...

class XxxBook(BaseBook[_XxxRow, Xxx]):  # CRUD
    model_cls = _XxxRow
    dto_cls = Xxx
```

这套结构本身是统一的（`BaseBook` 已经吸收了 Session 管理和
`_row_to_dto` 映射），但存在两类重复，且都源于同一笔历史债——
**早期开发时没有统一字段格式**：

1. **横向重复**：同一张表的 DTO 和 Row 字段几乎一一对应，每张表都要
   手写两遍同一份字段清单。`BaseBook._row_to_dto` 已经用
   `dataclasses.fields(dto_cls)` + `hasattr(row, f.name)` 按名字自动
   映射，因此字段声明之间的"呼应"靠命名约定而非显式关系维持。
2. **纵向重复**：`id`、`created_at`、`updated_at` 这类公共列在每张
   表里各写一遍，而且**格式没有对齐**（见 §3 盘点）。这是"欠债"，不是
   有意设计。

对照 [`guild/base.py`](../../magi/bus/guild/base.py) 里
`BaseJobRowMixin` 的先例——Job 层 10 个表共享 11 个队列控制列，通过一个
`__abstract__` 基类兜住全部——library 层缺少一个对等的
`BaseRecord` / DTO 基类。

## 2. 目标

**放弃向后兼容，直接重写，统一格式。**

- **时间戳统一成一种**：naive UTC `DateTime` + `default=utcnow_naive`
  （`created_at`）/ `onupdate=utcnow_naive`（`updated_at`）。
- **ID 统一成一种**（方向见 §4 待决策，尚未定）。
- 公共字段抽到 Row 侧一个 `BaseRecord` + dataclass 侧一个对应的 DTO
  基类；每个 Book 只声明自己的**业务字段**，横向 + 纵向重复一起消掉。
- "放弃向后兼容"意味着：旧 schema、旧数据、旧下游消费方一律不迁就，
  以新标准为准。

## 3. 现状盘点

### 3.1 时间戳：三套约定 + 一个笔误

| 表 | `created_at` | `updated_at` | 备注 |
|----|-------------|-------------|------|
| `memory_entries` | DateTime + default | DateTime + onupdate | 标准形态 ✅ |
| `contacts` / `contact_notes` | DateTime + default | DateTime + onupdate | ✅ |
| `magis` / `magis_admins` | DateTime + default | DateTime + onupdate | ✅ |
| `magis_roles` / `magis_memberships` | DateTime + default | DateTime + onupdate | ✅ |
| `mcp_servers` | DateTime + default | DateTime + onupdate | **标注笔误** `Mapped[DateTime]`（应为 `Mapped[datetime]`），且 `__table_args__` 重复声明两次 |
| `action_items` | DateTime + default | —（无 updated_at） | 只有 created_at |
| `hook_signoffs` | DateTime + default | — | 只有 created_at |
| `token_usage` | DateTime + default | — | 只有 created_at |
| `tasks` / `task_runs` | DateTime + default | DateTime + onupdate | ✅ |
| `settings` | — | DateTime + onupdate | 只有 updated_at |
| `chat_conversations` | **`String(32)`** | **`String(32)`** | ISO 字符串约定（含 `last_compaction_at`） |
| `chat_messages` | —（`ts` 为 `String(32)`） | — | ISO 字符串约定 |
| `runtime_state` | — | DateTime（**无 default**） | 由 `upsert`/`rename` 显式写 |
| `control_secrets` | DateTime（**无 default**） | — | 预留表，无写入方 |

**关键结论**：绝大多数时间戳已经是"标准形态"了；真正不统一的是
`chat_conversations` / `chat_messages` 这套**刻意的 ISO 字符串约定**
（`String(32)` + 写入方 `datetime.now(UTC).isoformat()`），它早于 bus
的 naive DateTime 规范化，且 WebUI/API/search 下游已依赖这些字符串。
这正是"欠债"最集中的地方。

### 3.2 主键：五种形态

| 形态 | 表 | 数量 |
|------|----|------|
| `id: int` 自增 | `memory_entries`, `token_usage`, `mcp_servers`, `contacts`, `contact_notes`, `action_items`, `tool_catalog_state`, `tool_definitions`, `hook_signoffs`, `magis`, `magis_admins`, `magis_roles`, `magis_memberships` | 12 |
| `id: String(26)` ULID | `tasks`, `task_runs`；`chat_conversations` 用 `conversation_id String(26)` | 3 |
| `key: String(255)` | `settings`, `control_settings` | 2 |
| `runtime_id: int` 无自增 | `runtime_state` | 1 |
| `name: String(100)` | `control_secrets` | 1 |

**结论**：主键无法抽成一个笼统的 `BaseRecord`（`BaseJobRowMixin` 那种
单继承兜底在这里不成立）；要么统一到一种形态（见 §4 决策），要么保持
"业务主键各自声明、审计列抽基类"的混合。

### 3.3 已知小问题（顺带修）

- `_McpServerRow.created_at` 标注为 `Mapped[DateTime]`，应为
  `Mapped[datetime]`（[`mcpServerBook.py:136`](../../magi/bus/library/local/mcpServerBook.py#L136)）。
- `_McpServerRow.__table_args__` 声明了两次（79 行与 152 行），后者
  覆盖前者，冗余。

## 4. 待决策项

以下三点未定，是重写方案的前置输入：

1. **ID 统一成什么？**
   - 选项 A：统一 `int` 自增主键（改动最小，但 `tasks`/`conversation`
     这类已有 ULID 语义的表要放弃字符串 ID）。
   - 选项 B：统一字符串 ULID `String(26)`（对齐 `tasks`/`conversation`
     现状，但 12 张 int 表要迁移）。
   - 选项 C：混合——审计列（`id`/`created_at`/`updated_at`）抽基类，
     业务主键各自声明（不追求"一种 ID"）。
2. **"放弃向后兼容"的边界**：旧 SQLite/PG 数据直接丢弃重建（不写
   迁移），还是接受一次性迁移脚本搬存量？
3. **重写范围**：全部 21 张表一次性统一，还是分波（先 local、后
   magis、chat 的 ISO 字符串约定最后单独处理）？

## 5. 建议方向（待决策后细化）

一旦 §4 定了，推荐按此落地：

```python
# magi/bus/library/base.py（放在 BaseBook 旁）

class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )

class UpdatedAtMixin:
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

# 若 §4 选 A：再抽 AutoIntIDMixin（int 自增主键）
class AutoIntIDMixin:
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
```

- Row 侧通过多重继承组合：`class _MemoryRow(Base, AutoIntIDMixin, CreatedAtMixin, UpdatedAtMixin)`。
- DTO 侧对应抽一个带 `id` / `created_at` / `updated_at` 的基类，业务
  字段子类声明。
- `BaseBook._row_to_dto` 无需改：已按字段名映射，mixin 带来的列会自动
  对上同名 dataclass 字段。
- chat 的 ISO 字符串约定若最终要统一，是**独立的、更大的改动**
  （列类型 + 写入方 + 下游 + 存量迁移），建议与 mixin 抽取分开推进。

## 6. 收益与风险（诚实评估）

- **收益**：约 12 处 `id` + 13 处 `created_at` + 11 处 `updated_at`
  的声明收拢到 2–3 个 mixin，列语义（naive UTC、default/onupdate）集中
  到一处；新 Book 搭 Row 骨架更省事。
- **风险**：每处样板原本仅 1–5 行，且附带表特定注释（如 `mcp_servers`
  的"name 非 PK 因为 SQLite 拒绝复合主键自增"），抽 mixin 后需安置这些
  上下文；多重继承对 Pylance 的 `Mapped` 列识别略吃力，需验证
  `alembic diff` 无 schema 漂移后再铺开。
- **定性**：锦上添花的重构，非欠债必还；真正"欠债"的是格式不统一
  （尤其 chat 的 ISO 字符串约定），那部分的价值远大于 mixin 本身。
