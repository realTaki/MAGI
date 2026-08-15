---
title: Book 层 Schema 统一方案
description: 已决策的 Book、Record 与时间/身份模型：三基类、统一自增 ID、按需 UID，以及全库 DateTime。
permalink: /insights/book-schema-unification/
---

# Book 层 Schema 统一方案

> **状态：已实施**（2026-08-15）
>
> 本文覆盖此前的 review 提案。实施时不保留旧 schema、旧数据格式或旧的
> 字符串主键兼容路径；所有受影响的 Row、Book、Job、API、WebUI 与测试必须
> 在同一轮重写中切换到本文定义的模型。

## 1. 背景与问题

`magi.bus.library` 的 Book 已有清晰的三层边界：公开 dataclass DTO、内部 ORM
Row、以及负责 CRUD 的 Book。`BaseBook` 已统一 Session 管理和 Row 到 DTO 的
自动映射，但各表仍自行重复声明公共身份与审计字段。

当前 schema 还同时存在自增整数、ULID 字符串、`key`、`name`、`runtime_id`
等多种主键，以及 `DateTime`、ISO 字符串 `String(32)`、手写
`datetime.now(UTC).isoformat()` 等多种时间表示。这使外键语义不一致，也让
时间序列化散落在业务代码中。

目标不是只做样板去重，而是建立唯一、可预测的记录模型：数据库内部、Book DTO 与
API 模型统一以整数关系和 `datetime` 工作；JSON 传输由框架编码，展示格式只由前端决定。

## 2. 已决策的模型

整个 library 层只引入并使用以下三种基类；不拆分为多个 ID、创建时间或更新时间
mixin。

```python
# magi/bus/library/base.py

class BaseBook[RowT: BaseRecordMixin, DtoT: BaseRecord]:
    """Book 的 Session、DTO/Row 映射及通用新增、读取行为。"""


@dataclass(frozen=True, slots=True, kw_only=True)
class BaseRecord:
    """所有公开持久化记录共有的 DTO 字段。"""

    id: int = field(default=0, init=False)
    created_at: datetime | None = field(default=None, init=False)
    updated_at: datetime | None = field(default=None, init=False)


class BaseRecordMixin(Base):
    """所有 library ORM Row 的唯一公共持久化骨架。"""

    __abstract__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )
```

每个 Row 直接继承 `BaseRecordMixin`，不再直接继承 `Base`；每个公开的持久化
DTO 继承 `BaseRecord`。`kw_only=True` 让基类字段不会与子类业务字段的默认值
排序冲突，DTO 一律使用具名构造。

`BaseBook._row_to_dto()` 不转换时间：ORM Row 与 DTO 均保留同一个 naive UTC
`datetime`。FastAPI 的标准 JSON 编码只负责传输；WebUI 在展示层按用户时区和格式
需求转换。Book、API、worker 与业务逻辑不得自行调用 `isoformat()`。

### 2.1 新增命令契约

所有 library Book 只有一个普通新增入口，由基类实现：

```python
record_id = book.add(record)
```

调用方构造未持久化的 `BaseRecord` 子类；`BaseBook.add(record)` 在一个事务中创建
Row 并仅返回数据库生成的内部 `int id`。它不是 DTO 工厂：如需完整记录，调用方明确
使用 `get(...)`、`get_by_id(...)` 或 `list(...)` 回读。

`id`、`created_at`、`updated_at` 均为 `init=False` 的数据库所有字段，调用方不能在
Record 构造时指定。`BaseBook._row_to_dto()` 在读取持久化 Row 后内部回填它们。业务
键（如 `conversation_id`、`task_id`）属于 Record 的正常输入字段；需要自动生成时由
其 DTO 的 `default_factory` 生成，不能改用数据库内部 `id`。

`BaseBook` 默认按同名 DTO 字段写入同名 ORM 列。字段需要领域校验、业务键到物理外键
解析或编码（例如 `conversation_id -> conversation_row_id`、MCP JSON 列）时，子类仅覆写
`_validate_add(record)` / `_record_to_row_values(record, session)` 钩子；不得重新定义
`add`。幂等创建、upsert 和其他复合业务操作保留领域明确的方法名，并在内部复用该写入
契约。

## 3. 身份与外键规则

### 3.1 内部 ID

每张 library 表均有 `id: INTEGER PRIMARY KEY AUTOINCREMENT`，由
`BaseRecordMixin` 提供。它是唯一的数据库主键；所有物理外键均引用目标表的
`id`。通用读取、删除和内部关联以这个整数 ID 为准。

### 3.2 业务键

业务需要的身份不再充当主键：

- 需要跨进程、跨 API、跨 Job 载荷或公开 URL 的稳定身份，使用领域明确的
  唯一键，例如 `conversation_id`、`task_id`、`message_id`、`run_id`；必须
  `unique=True`、`nullable=False`。不得以缺乏领域语义的通用 `uid` 取代它们。
- `name` / `key`：保留为业务唯一键或检索键，添加对应 `UniqueConstraint`，
  但不是主键。
- 依附其他记录的一对一业务关系使用语义化的整数外键，例如
  `membership_id UNIQUE REFERENCES magis_memberships(id)`，而不是让
  `runtime_id` 兼任本表主键与外键。

因此，现有字符串 `tasks.id`、`task_runs.id` 必须分别迁移为明确的业务唯一键，
如 `task_id`、`run_id`；`chat_conversations.conversation_id` 保留为会话业务键。
子表的物理关系使用语义化的整数外键，例如 `conversation_row_id`、
`task_row_id`，指向父表 `id`。对外 API 和异步 Job 传递业务键，进入 Book 后
解析为内部 `id`。

## 4. 时间规则

数据库与 ORM 只有一种时间表示：**naive UTC `datetime` / SQLAlchemy
`DateTime`**。唯一的“当前时间”入口是 `utcnow_naive()`。

以下规则适用于所有表和所有业务时间字段，而不仅是审计列：

- `created_at`、`updated_at` 由 `BaseRecordMixin` 提供，前者有默认值，后者有
  默认值和 `onupdate`。
- `started_at`、`finished_at`、`run_at`、`last_seen_at`、`last_compaction_at`
  等业务时间均为 `datetime` / `DateTime`。
- 现有 `chat_conversations` 的 `created_at` / `updated_at` /
  `last_compaction_at`、`chat_messages.ts`、以及其他 `String(32)` 时间列必须
  删除字符串存储方式，必要时将 `ts` 重命名为语义明确的 `occurred_at`。
- 代码不得存储 `datetime.now(UTC).isoformat()`、`isoformat() + "Z"` 或任何
  时间字符串；外部输入 ISO 字符串必须在入口通过唯一的解析函数归一为 naive UTC。
- 后端 DTO 与 API 模型不手工格式化时间；FastAPI 和工具结果等传输边界集中编码标准
  ISO 时间，前端负责人类可读的时区和格式转换。

这消除后端业务代码中的重复 ISO 转换和时区格式漂移。

## 5. 实施边界

本次是全链路 schema 重写，不采用旧 schema 的兼容包装器，也不保留双写、旧列
回退或旧字符串 ID 的读取分支。旧 SQLite / PostgreSQL 数据库按新的基线 schema
重建；不为旧数据编写迁移脚本。

“不保留兼容”不等于忽略现有消费者。所有引用旧主键、外键或时间字符串的调用点
必须同步更新，包括：

- 各 Book 的 `add(record) -> int` 签名、查询、约束与 DTO；
- library 与 guild 间的 Job 载荷和外键；
- Conversation、Task、TaskRun、Message 的关联、幂等与 FTS 逻辑；
- API 路由、请求/响应模型、WebUI 调用方与工具层；
- 初始化基线、测试夹具和端到端测试。

实施顺序应以引用关系而非目录分波：先建立三基类与时间转换边界，再重写被最多
下游引用的 identity 链（Conversation/Message、Task/TaskRun、MAGIS runtime），
随后迁移其余 Book，最后删除所有旧 schema 与转换残留。

## 6. 顺带修复与验收

- `_McpServerRow` 的时间类型标注使用 `Mapped[datetime]`，不使用
  `Mapped[DateTime]`。
- `_McpServerRow.__table_args__` 合并为一次声明，保留 `UniqueConstraint` 与
  `extend_existing`。
- 所有 library Row 继承 `BaseRecordMixin`，所有持久化 DTO 继承 `BaseRecord`。
- 所有 library 表均具有自增 `id`、`created_at` 和 `updated_at`；没有字符串时间列。
- 所有物理外键均指向整数 `id`；每个领域业务键、`name` 或 `key` 的唯一性由明确约束
  表达。
- `rg` 不再找到业务代码中的手写 ISO 时间写入或展示格式化；后端 API 模型直接传递
  `datetime`，由前端负责时区和人类可读格式。
- Alembic 基线、SQLite 与 PostgreSQL 建表结果一致，并通过完整测试套件。

## 7. 收益与代价

收益是一个固定的认知模型：每张表都有相同的内部身份与审计时间，关系均为整数
外键，公开身份显式为领域业务键，时间在唯一边界序列化。新 Book 只需声明业务字段，
BaseBook 的自动映射保持有效。

代价是身份模型与时间存储的广泛替换，尤其涉及 Conversation、Task、Job 与 API。
这是有意承担的一次性重写成本；完成后不再维护任何旧 schema 或兼容路径。
