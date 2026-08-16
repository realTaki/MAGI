# Book Record 去 Pydantic 与长度校验审计

日期：2026-08-15  
状态：核心迁移已实施（2026-08-15）

## 1. 本次决策

`magi.bus.library` 是持久化领域层，不是 API、Channel、Tool 或 LLM Provider 的输入
防护层。所有持久化 DTO 将回归标准库 `@dataclass(frozen=True, slots=True, kw_only=True)`；
不再以 `pydantic.dataclasses.dataclass` 包装，也不在 DTO 字段上声明 `Annotated`、
`Strict`、`StringConstraints` 或 Pydantic `Field`。

`BaseBook.add(record)` 与 `BaseBook.update(record)` 已共同调用 `_validate_add(record)`。
因此只有真正的跨字段、跨行、外键映射或调度语义保留在该钩子/明确的领域方法中。普通
类型、枚举转换、文本长度、展示用 trim 不属于 BUS 的职责。

## 2. 目标边界

| 层 | 负责内容 | 不负责内容 |
| --- | --- | --- |
| BUS Record / Book | dataclass 形状、SQL 映射、数据库约束、领域不变量 | HTTP 负载长度、LLM token 预算、展示格式 |
| API / Channel / Tool | 各入口自己的格式和轻量防滥用限制 | 代替 Book 的跨表完整性 |
| Agent / Provider adapter | 按实际模型与 token 预算压缩或拒绝上下文；处理 Provider 上下文超限 | 用字符数假装 token 上限 |
| 数据库 | NOT NULL、FK、UNIQUE、持久化类型 | 全局产品输入策略 |

这意味着 `Task.prompt`、`Memory.body`、`ContactNote.note`、`Message.text` 等自由文本
不再在 BUS 硬编码字符/字节上限。若某个入口需要保护，应在该入口设定其自身限制；真正的
模型上下文控制放到调用 Provider 前。

## 3. 基础设施改动

### 3.1 `base.py`

当前 `record()` 是 `pydantic_dataclass(...)` 的包装，并为类型检查额外维护
`dataclass_transform`、`ConfigDict`、`Field`、`cast`。应删除这些 Pydantic 依赖，改为一个
标准库 dataclass 包装器，保留统一的 `frozen=True, slots=True, kw_only=True`：

```python
def record[RecordT: BaseRecord](cls: type[RecordT]) -> type[RecordT]:
    return dataclasses.dataclass(cls, frozen=True, slots=True, kw_only=True)
```

也可以直接让每个子类写 `@dataclasses.dataclass(...)`；保留小型 `@record` 仅为避免重复
三个 dataclass 选项。它不再表达“验证”。`BaseRecord.with_changes()` 仍需保留：普通
`dataclasses.replace()` 会丢弃 `init=False` 的 `id`、`created_at`、`updated_at`，而该方法
必须保留这些数据库拥有字段再交给 `update(record)`。

同步更新：`magi/bus/library/__init__.py` 的导出、`base.py` docstring、以及
`BOOK_SCHEMA_UNIFICATION_REVIEW.md` 中“Pydantic 构造期校验”的表述。

### 3.2 Record 声明的机械替换

以下 14 个 Record 文件的所有 `@record` Record 都要去除 `Annotated[...]` 外壳与 Pydantic import：

| 文件 | Record |
| --- | --- |
| `local/actionItemBook.py` | `ActionItem` |
| `local/contactBook.py` | `Contact`, `ContactNote` |
| `local/conversationBook.py` | `Conversation`, `Message` |
| `local/hookSignoffBook.py` | `HookSignoff` |
| `local/memoryBook.py` | `Memory` |
| `local/tasksBook.py` | `Task`, `TaskRun` |
| `local/tokenUsageBook.py` | `TokenUsage` |
| `magis/magisBook.py` | `Magis`, `MagisAdmin` |
| `magis/runtimeBook.py` | `Runtime`, `ControlSecret` |

另外 `local/toolsBook.py`、`local/mcpServerBook.py`、`local/settingBook.py`、
`magis/controlSettingBook.py`、`magis/membershipBook.py` 虽没有字段约束，也必须随
`@record` 的实现一并回归普通 dataclass 并做构造/映射回归测试。

## 4. 应删除的 BUS 限制

以下是当前纯 Pydantic DTO 约束；除非某字段被第 5 节明确列为领域不变量，否则全部移除：

- `ActionItem`：标题、描述、URL、完成说明的长度/trim；`contact_id`、时间、布尔 strict。
- `Contact` / `ContactNote`：姓名、显示名、笔记正文的长度/trim；标识和时间 strict。
- `Conversation` / `Message`：业务 ID、投递地址、channel、标题、role、attempt ID 的长度；
  `archived` 的 `0..1`、时间 strict。
- `Memory`：subject/body 的非空、trim、长度；priority `1..5`；时间 strict。
- `Task` / `TaskRun`：name/prompt、各种语义 ID 的长度；`enabled`、失败次数、延迟、token
  计数等数值范围；时间 strict。
- `TokenUsage`：provider/model/attempt ID 的长度，计数与费用的非负范围。
- `Magis` / `MagisAdmin`：姓名长度/trim、`auth_mode` 的正则、所有 strict。
- `Runtime` / `ControlSecret`：运行时名称、路径、镜像、URL、部署字段长度，以及 strict。
- `HookSignoff`：时间 strict。

移除后，SQLAlchemy Row 的 `String(n)` 是否保留是独立 schema 决策：SQLite 对长度通常不
强制，PostgreSQL 的 `VARCHAR(n)` 会强制。若产品也不需要数据库长度上限，应在后续 schema
审计中将它们改为 `Text`/无长度 `String`；本轮不应悄悄混入 Alembic schema 迁移。

## 5. 必须留在 BUS 的规则

这些规则与入口无关，必须保留或从现有映射代码明确抽到 `_validate_add`：

| 领域 | 规则与当前位置 |
| --- | --- |
| Task | cron 与 `run_at` 必须 XOR、cron 可解析、一次性任务时间有效且在未来：`TaskBook._validate_add` |
| Task / TaskRun | `conversation_id`、`task_id` 必须解析到对应 Row：`_record_to_row_values` |
| Membership | role 存在且属于目标 MAGIS：`MagisMembershipBook._record_to_row_values` |
| Contact / MagisAdmin | Telegram 标识不可被同一作用域其他 Row 占用：当前 `_record_to_row_values`/专用绑定方法 |
| McpServer | transport 类型与 `command` / `url` 的组合：`McpServerBook.upsert` |
| Runtime | 端口、运行时身份、状态转换与资源回收：`RuntimeBook` 的明确领域方法 |
| 数据库 | 表级 UNIQUE、FK、NOT NULL 与级联删除必须继续由 schema 保证 |

注意：目前 `ContactBook._record_to_row_values`、`ContactNoteBook._record_to_row_values`、
`MagisAdminBook._record_to_row_values` 还承担 `strip()` 和截断。这些是展示/入口规范化，
不应在 BUS 静默改变输入；应删除截断，必要时由 API/Tool 在自己的入口显式处理。

## 6. 入口迁移清单

移除 Record 校验会使原本依赖 DTO 构造抛出 `ValidationError` 的路径失效。需要逐个确认
以下入口已拥有合适的错误处理，而不是把无效值推迟成数据库 driver 错误：

| 入口 | 现状 | 后续动作 |
| --- | --- | --- |
| `channels/api/contacts.py` | Pydantic 请求模型已有 name/display-name 限制 | 保留或按 UI 需求放宽；Book 不再 trim |
| `channels/api/tasks.py` | `TaskIn` / `TaskPatch` 有 name/prompt 限制 | 保留为 API 策略；调度规则仍由 Book 拒绝 |
| `channels/api/chat.py` | 有 `_MAX_INPUT_CHARS` | 保持该入口保护；Provider 前仍需 token 控制 |
| `channels/api/chat_conversations.py` | title 限制与手动截断 | 明确这是 API 展示策略，不再声称与 DTO 一致 |
| `channels/api/magis.py` | name/instruction/role/responsibility 限制 | 保留为管理 UI 策略 |
| `channels/api/mcp_servers.py` | name、command、URL 限制 | 保留协议/管理入口限制；`upsert` 保留 transport 语义 |
| `tools/memory/*`, `tools/tasks/*`, `tools/mcp/*` | 多数直接构造 Record | 为每个 tool schema/参数增加仅必要的轻量限制，并保留 Book 领域错误转换 |
| `channels/telegram/*` | Telegram payload 有自己的平台限制 | 在 adapter 层处理，不让 Message Record 截断 |
| Agent / Provider 调用路径 | 目前字符上限不能代表 token 预算 | 统一检查真实 token 预算、压缩与 Provider overflow 错误 |

认证验证码、Telegram token、URL、端口、搜索分页等 API 限制不是 DTO 迁移对象，保持在其
各自 API/adapter 层。

## 7. 测试需要重写的地方

`tests/unit/test_bus_books.py` 当前包含“Record constraints”“Pydantic validation error”以及
`Strict` 拒绝 ISO 时间字符串等断言。这些应删除或改为：

1. dataclass 构造与 `add/get/update/delete` 的映射测试；
2. `_validate_add` 的 Task 调度语义测试；
3. FK/唯一性/业务键解析失败测试；
4. API、Tool、Channel 各自的入口长度/格式测试；
5. Provider token-budget/overflow 处理测试（该能力存在后）。

不能继续断言 `Memory(...)`、`ActionItem(...)` 等在 DTO 构造时会拒绝过长文本或字符串时间。

## 8. 推荐实施顺序

1. 先将 `base.py` 的 `record` 改为标准 dataclass 包装器，并删除 Pydantic 依赖。
2. 机械去除 14 个 Record 文件的 `Annotated` / Pydantic import；不同时做数据库长度 schema 迁移。
3. 删除 BUS 中的 trim、截断、普通类型/长度检查；保留第 5 节规则。
4. 逐条迁移/补齐 API、Tool、Telegram 的入口保护；为 Provider 前 token 控制单列实现任务。
5. 重写 Book 与入口测试，最后运行全量单元测试、Ruff、类型检查及 `git diff --check`。

## 9. 验收标准

- `magi.bus.library` 不再导入 `pydantic` 或 `pydantic.dataclasses`。
- 所有 Library Record 是普通 frozen、slots、keyword-only dataclass。
- 所有 Book 的普通 CRUD 仍只由 `BaseBook` 实现。
- `_validate_add` 只包含真正的领域不变量；没有长度、类型强制或静默文本截断。
- API/Tool/Channel 的入口限制有对应测试；Provider 前有独立 token 策略或明确的 overflow
  错误处理。
- 原有 Book、Task、MCP、MAGIS、A2A 回归测试通过。

## 10. 本次实施结果

已完成：

- `BaseRecord`/`@record` 已回归标准库 dataclass，`magi.bus.library` 不再导入 Pydantic；
- 所有 Library Record 已移除 Pydantic 字段包装与 BUS DTO 长度/strict 校验；
- Message、ContactNote、ActionItem、Conversation 标题等持久化路径不再截断或静默 trim；
- 已删除失效的全局 `system.chat_max_input_chars` 设置与 MessageBook 截断逻辑；
- Book 测试改为验证映射、数据库约束与 Task 调度语义；Tool 测试改为验证入口自身的命令
  形状和 vocabulary。

尚未作为本次 Record/Book 迁移的一部分实现：Provider 调用前的真实 token 预算器。这是独立
的 Agent/Provider 行为，当前仍由既有压缩和 Provider 错误路径处理，后续应单独设计和测试。
