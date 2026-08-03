# MAGI BUS-Centric Architecture Refactor Plan

> 本文是交给 Codex 执行的架构重构任务书。它描述目标边界、不可违反的依赖规则、建议 API、迁移阶段与验收标准。执行时应以仓库当前 `main` 为基线，先检查实际代码，再逐阶段提交可运行、可测试的修改。

## 1. 任务目标

将 MAGI 重构为以 `magi.bus` 为唯一跨模块通信和数据访问边界的架构：

```text
agent     ─┐
tools      ├──> bus ───> db ───> Local SQLite / MAGIS PostgreSQL
channels  ─┘
```

核心要求：

1. `agent`、`tools`、`channels` 之间不得直接导入或调用。
2. `agent`、`tools`、`channels` 不得直接导入或操作 `magi.db`、SQLAlchemy model、Session 或 engine。
3. 所有跨模块状态、命令、查询、结果和 effect 都通过 `magi.bus` 的公开 contracts/API 交换。
4. `magi.bus` 负责读写协议、事务、幂等、lease、outbox、存储路由和数据 invariant。
5. `magi.db` 只负责 models、engine/session factory、migrations 和数据库特有配置。
6. Local SQLite 与 MAGIS PostgreSQL 都只能经由 BUS 访问。
7. 数据库中的 Tool Catalog 是 Agent 可见工具定义的唯一事实来源；Agent 不得读取 `tools.registry` 或 Tool 类。
8. Tool、Channel 和其他外部副作用仍由各自 worker 执行；BUS 不执行工具、不调用 LLM、不访问 Telegram/A2A 网络。

## 2. 架构含义

这里的 BUS 不只是 message queue。它是 MAGI 内部的 protocol/data plane，包含：

- 跨模块 DTO、Command、Event 和 Query contracts；
- Local SQLite 与 MAGIS PostgreSQL 的统一访问门面；
- repository/application service；
- durable inbox、jobs、outbox、run continuation；
- 事务、幂等、lease、重试与恢复；
- 数据权限和持久化 invariant；
- 进程内 wake-up/stream 信号所依赖的持久化状态边界。

BUS 可以是同进程 Python 模块，不要求部署为独立服务。架构边界由 import 和 API 约束保证，而不是由网络边界保证。

BUS 只拥有“如何可靠读写和交换状态”的逻辑，不拥有领域决策：

- Agent 决定下一步推理和产生哪些 intents；
- ToolWorker 决定如何加载、校验和执行工具实现；
- Channel worker 决定如何调用 Telegram、WebUI、A2A 等外部协议；
- Agent 内部 LLM provider 仍可由 Agent 调用，LLM I/O 不放进数据库事务；
- BUS 只接收 intents、持久化它们，并将 work/result 通过协议交给对应 worker。

## 3. 强制依赖规则

### 3.1 允许的依赖

```text
magi.agent.*       -> magi.bus public API
magi.tools.*       -> magi.bus public API
magi.channels.*    -> magi.bus public API
magi.bus.*         -> magi.db.*
magi.db.*          -> Python/SQLAlchemy/database drivers only

agent internals    -> agent.llm / agent prompt/context internals
tools internals    -> tool implementations/local execution registry
channels internals -> Telegram/WebUI/A2A/task protocol implementations
```

`magi.__main__` 是 composition root。它应调用 `bus.bootstrap()`、启动各 worker 和 channel，不应让领域模块自行初始化 DB。

### 3.2 禁止的依赖

```text
agent    -X-> tools
agent    -X-> channels
agent    -X-> db

tools    -X-> agent
tools    -X-> channels
tools    -X-> db

channels -X-> agent
channels -X-> tools
channels -X-> db

db       -X-> bus / agent / tools / channels
bus      -X-> Tool classes / Telegram clients / AgentWorker / LLM providers
```

`mcp`、`proactive`、`connectors` 和运行时 API 也遵循相同的持久化规则：不得绕过 BUS 直接访问 DB。它们可以访问自己拥有的外部客户端或解析器，但跨模块交换必须经过 BUS。

### 3.3 不泄漏 ORM

BUS 公共 API 只能返回不可变 dataclass/Pydantic DTO、primitive 或 JSON-safe payload。不得返回：

- SQLAlchemy ORM model；
- Session/Connection/Query；
- lazy relationship；
- 依赖某个数据库方言的对象。

## 4. 模块职责

### 4.1 `magi.db`

目标职责：

```text
magi/db/
├── base.py
├── engines/
│   ├── local_sqlite.py
│   └── magis_postgres.py
├── models/
│   ├── local/
│   └── magis/
├── migrations/
└── sessions.py
```

只提供：

- declarative base 与 models；
- Local SQLite、MAGIS PostgreSQL engine/session factory；
- Alembic migrations；
- WAL、busy timeout、pool 等数据库配置；
- 供 BUS 内部 repository 使用的底层 session context。

禁止在 DB 模块提供带领域含义的操作，例如 `publish_agent_message()`、`list_tools_for_agent()`、`complete_tool_job()`。

不要求第一阶段立刻移动所有现有 model 文件；可以先保留文件位置，优先完成依赖方向。目录整理放在迁移后期，避免同时改变行为和路径。

### 4.2 `magi.bus`

建议拆成明确子域，避免形成单个 God class：

```text
magi/bus/
├── contracts/
│   ├── common.py
│   ├── agent.py
│   ├── tools.py
│   ├── channels.py
│   ├── memory.py
│   └── magis.py
├── services/
│   ├── agent_runs.py
│   ├── tool_catalog.py
│   ├── tool_jobs.py
│   ├── delivery.py
│   ├── sessions.py
│   ├── memory.py
│   ├── tasks.py
│   ├── settings.py
│   └── magis.py
├── repositories/
│   ├── local_sqlite/
│   └── magis_postgres/
├── stream.py
├── bootstrap.py
└── __init__.py
```

现有 `BusStore` 可以逐步拆分，不能一次性把所有新方法继续堆到同一个类中。公开入口应是按领域划分的 facade，例如：

```python
bus.agent_runs.publish_input(...)
bus.agent_runs.claim_next(...)
bus.agent_runs.commit_transition(...)

bus.tool_catalog.replace_snapshot(...)
bus.tool_catalog.list_schemas(...)

bus.tool_jobs.claim_next(...)
bus.tool_jobs.complete(...)

bus.delivery.enqueue(...)
bus.delivery.claim_next(...)
bus.delivery.complete(...)

bus.sessions.get_transcript(...)
bus.memory.search(...)
bus.magis.get_runtime_identity(...)
```

### 4.3 `magi.agent`

Agent 负责：

- 从 BUS claim durable inbox；
- 通过 BUS 获取 session、memory、runtime identity、provider configuration 和 tool schemas；
- 构造 prompt/context；
- 在事务外进行一次完整、可流式的 LLM inference；
- 产生纯 DTO intents；
- 通过 BUS 原子提交 run transition、tool calls、delivery/A2A intents；
- 从 BUS 接收 tool/A2A result 并恢复 continuation。

Agent 不得：

- 导入 Tool、tool registry 或 channel adapter；
- 导入 ORM model 或打开 DB session；
- 直接执行工具；
- 直接发送 Telegram/A2A；
- 直接写 session/memory 表。

### 4.4 `magi.tools`

Tools 模块内部可以保留 Python Tool implementation registry，但该 registry 只用于 ToolWorker 执行，不能成为 Agent 的工具定义来源。

Tools 负责：

- 发现 built-in、MCP、Skill-backed 工具；
- 将规范化 ToolDefinition snapshot 发布给 BUS；
- 从 BUS claim tool job；
- 在事务外执行工具实现；
- 通过 BUS durable complete/fail job；
- 对于发消息、A2A 等跨模块 effect，只向 BUS 写 intent，不直接调用 Channel。

### 4.5 `magi.channels`

Channels 负责：

- 将 WebUI、Telegram、Task、A2A ingress 标准化后发布给 BUS；
- 从 BUS claim delivery outbox；
- 在事务外调用外部协议；
- 将 delivery 成功、失败、重试结果写回 BUS；
- WebUI 通过 BUS 查询 run、session、settings、MAGIS 等数据；
- SSE 使用 StreamHub 接收 best-effort delta，断线后通过 BUS 读取 durable result。

Channels 不得导入 AgentWorker、Tool、ORM 或 DB session。

## 5. Tool Catalog 设计

### 5.1 事实来源

数据库中的 Tool Catalog 是 Agent 可见 tool schema 的唯一事实来源。进程内 registry 只负责执行映射：

```text
Tool implementation registry -> ToolWorker execution only
Database Tool Catalog         -> Agent prompt/tool schemas
```

### 5.2 建议 DTO

```python
@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    source: str
    description: str
    input_schema: dict[str, Any]
    allowed_roles: tuple[str, ...]
    enabled: bool
    implementation_version: str | None
    schema_hash: str
    revision: int
```

### 5.3 建议 Local SQLite models

新增或规范化：

```text
tool_definitions
├── id
├── name
├── source                 # builtin / mcp:<server> / skill:<name>
├── description
├── input_schema_json
├── allowed_roles_json
├── enabled
├── implementation_version
├── schema_hash
├── revision
├── created_at
└── updated_at

tool_catalog_state
├── singleton_key
├── current_revision
├── snapshot_hash
└── updated_at
```

唯一约束至少覆盖 `(source, name)`；如果运行时要求工具名全局唯一，则 BUS 在发布 snapshot 时拒绝冲突，并返回明确错误。

### 5.4 Snapshot 更新协议

Tool/MCP/Skill loader 在以下时间发布 snapshot：

- runtime 启动；
- MCP server 新增、删除、启用、禁用或 schema 改变；
- Skill/tool bundle 发生变化；
- operator 显式 refresh。

调用：

```python
bus.tool_catalog.replace_snapshot(
    source="builtin",
    definitions=[...],
    expected_previous_revision=revision,
)
```

BUS 在一次 Local SQLite transaction 中：

1. 校验名称、schema 和冲突；
2. upsert 当前 definitions；
3. 将该 source 中已消失的工具标记为 disabled；
4. 增加 catalog revision；
5. 更新 snapshot hash；
6. 可选写入 `tool.catalog.updated` operational event；
7. transaction commit 后唤醒相关本地消费者。

### 5.5 Agent 读取协议

Agent 使用：

```python
schemas = bus.tool_catalog.list_schemas(
    caller_role=caller_role,
    enabled_only=True,
)
```

权限过滤必须由 BUS 使用 catalog 数据完成。Agent 不知道工具来自 built-in、MCP 还是 Skill，也不导入 Tool 类。

允许 BUS 按 revision 做只读缓存，但数据库始终是事实来源；缓存失效必须由 revision/snapshot hash 驱动，不能依赖 Agent 和 ToolWorker 共享进程内 registry。

### 5.6 Tool Job 一致性

创建 tool call/job 时保存：

- `tool_definition_id` 或稳定 name/source；
- `catalog_revision`；
- `schema_hash`；
- provider `tool_call_id`；
- arguments；
- idempotency key。

ToolWorker claim 后应验证当前 implementation 与 job 的 definition。若工具已删除、禁用或 schema 不兼容，不能崩溃或静默执行；应通过 BUS 写入 durable `tool.failed`，让 Agent 获得 provider-valid tool result。

## 6. Local 与 MAGIS 存储路由

建议路由：

| BUS 子域 | 存储 |
| --- | --- |
| agent inbox/runs/inputs/LLM attempts | Local SQLite |
| tool catalog/jobs/calls | Local SQLite |
| delivery outbox/A2A invocation | Local SQLite |
| sessions/messages/search | Local SQLite |
| memory/contacts/local bindings | Local SQLite |
| tasks/task runs/presets | Local SQLite |
| runtime settings/MCP config | Local SQLite |
| MAGIS/MAGIC/membership/roles/admins | MAGIS PostgreSQL |
| provider configuration/runtime identity | MAGIS PostgreSQL |
| EVE lifecycle/public society facts | MAGIS PostgreSQL |

BUS 对调用者提供统一门面，但不得伪造跨 SQLite/PostgreSQL 原子事务。

跨库流程必须使用 saga/outbox：

```text
Local transaction
  -> durable outbox
  -> worker performs MAGIS transaction
  -> result/event written back through BUS
```

禁止同时打开 Local 与 MAGIS session 后把两次 commit 当作一个原子操作。

## 7. 关键运行流程

### 7.1 普通消息

```text
Channel
  -> bus.agent_runs.publish_input
  -> Local agent_inbox
  -> AgentWorker claim
  -> BUS queries context/tool catalog/provider config
  -> Agent performs one LLM inference outside transaction
  -> bus.agent_runs.commit_transition
```

### 7.2 Tool call

```text
Agent intent
  -> BUS atomically writes continuation + tool_calls + tool_jobs
  -> ToolWorker claims job through BUS
  -> ToolWorker executes local implementation
  -> bus.tool_jobs.complete/fail
  -> BUS writes tool.result/tool.failed to agent inbox
  -> AgentWorker resumes same run
```

### 7.3 消息投递

```text
Agent/Tool intent
  -> bus.delivery.enqueue
  -> delivery_outbox
  -> Channel DeliveryWorker claims through BUS
  -> Telegram/A2A network call
  -> bus.delivery.complete/retry/fail
```

### 7.4 Steering

同一 conversation 在 active run 期间的新普通消息：

- 立即通过 BUS 持久化；
- 关联当前 run；
- 不取消当前 LLM/tool；
- 不创建并行 run；
- 在 provider transcript 已闭合所有 tool-use/tool-result 后，由 BUS 按接收顺序提供给 Agent 的下一次 inference。

## 8. 当前代码需要消除的耦合

执行前用 `rg` 重新确认，至少处理以下已知耦合：

- `magi/agent/runtime_context.py` 直接读取 `magi.tools.registry`；
- `magi/agent/llm/factory.py` 直接访问 MAGIS ORM；
- `magi/agent/memory/**` 直接访问 DB/session stores；
- `magi/channels/webui/api/**` 大量直接访问 ORM；
- `magi/channels/tasks/**` 直接访问 Task models/session stores；
- `magi/channels/dispatcher.py` 直接访问联系人或 IM binding；
- `magi/channels/telegram/**` 直接调用 Agent submission helper；
- `magi/tools/**` 中直接访问 memory、session、settings、contacts 或 channel dispatcher 的工具；
- MCP loader/management 直接访问 `mcp_servers` models；
- `magi/agent/loop.py` 保留旧的直接 tool execution 和 plugin hooks。

重构后，旧 helper 可以短期变成 BUS facade 的兼容 wrapper，但兼容 wrapper 必须位于 `magi.bus` 或只调用 BUS；不得继续保留跨模块直接引用。

## 9. 分阶段实施计划

每个阶段必须保持可运行并增加测试。不要先删除旧路径再补新路径。

### Phase 0：基线与边界测试

1. 记录当前测试结果，区分真实回归与已经过期的 legacy tests。
2. 新增 architecture/import boundary test。
3. 修正明显仍 patch `handle_message()` 的旧测试，使测试能够覆盖当前 Actor path。
4. 不在此阶段改变运行行为。

### Phase 1：BUS contracts 与 facades

1. 建立按领域划分的 BUS contracts。
2. DTO 与 ORM model 完全分离。
3. 将现有 `BusStore` 包装/拆分为 `agent_runs`、`tool_jobs`、`delivery` services。
4. 增加 Local/MAGIS repository adapter。
5. 提供 `bus.bootstrap()` 统一初始化入口。

### Phase 2：数据库 Tool Catalog

1. 添加 Tool Catalog models 和 Alembic migration。
2. 实现 snapshot replace、revision、hash、权限过滤和查询 API。
3. Tool registry/MCP loader/Skill loader 在启动和更新时向 BUS 发布 snapshot。
4. Agent 仍可暂时走旧 schema path，但增加一致性对比测试。

### Phase 3：Agent 只依赖 BUS

1. `runtime_context` 改为从 BUS 获取 tool schemas。
2. provider configuration/runtime identity 改为 BUS query。
3. session、memory、contacts、settings 改为 BUS query/command。
4. Agent transition 只产生/提交 DTO intents。
5. 移除 `agent -> tools/channels/db` imports。

### Phase 4：Tools 只依赖 BUS

1. ToolWorker 通过 BUS claim/complete job。
2. 所有工具的 session/memory/contact/settings 访问迁移到 BUS。
3. `send_message`、`message_magi` 等工具只写 BUS intent。
4. Tool hooks 移到 BUS job lifecycle 或新 ToolWorker path，不能只存在于 legacy loop。
5. 移除 `tools -> agent/channels/db` imports。

### Phase 5：Channels 只依赖 BUS

1. WebUI/TG/Task/A2A ingress 只调用 BUS publish API。
2. WebUI API 的 session、settings、contacts、tasks、MAGIS 查询全部走 BUS。
3. DeliveryWorker 只通过 BUS claim/complete/retry。
4. Channel dispatcher 不再读取 ORM 或调用 Tool/Agent。
5. 移除 `channels -> agent/tools/db` imports。

### Phase 6：扩展模块统一

1. MCP config CRUD 改走 BUS。
2. proactive/task preset 持久化改走 BUS。
3. connectors 的持久化与事件投递改走 BUS。
4. orchestrator 对 MAGIS 事实的访问改走 BUS，但 Kubernetes API 仍由 orchestrator 自己调用。
5. plugin hooks 接入新的 Agent/Tool/Delivery 生命周期。

### Phase 7：删除 legacy 路径与清理 DB

1. 在所有生产调用方迁移后删除 `agent.loop.handle_message()` 或将其缩减为明确的测试/兼容 facade。
2. 删除旧的 tool schema 共享 cache 对 Agent 的可见性。
3. 删除领域模块中的 DB compatibility imports。
4. 视风险移动 DB models 目录；这一步不应和核心行为迁移混在同一 commit。
5. 更新 README、ARCHITECTURE、runtime design 和过期 docstring。

### Phase 8：完整验证

1. Unit tests：BUS services、repositories、catalog revision、jobs、delivery、steering。
2. Contract tests：SQLite 与 MAGIS repository 对相同 facade contract 的行为。
3. Integration tests：WebUI/TG/Task/A2A -> Agent -> Tool -> Agent -> Delivery。
4. Restart/recovery tests：waiting tool、waiting A2A、expired lease、duplicate delivery。
5. Security tests：role-filtered tool catalog、unauthorized job、cross-MAGIS A2A、ORM leakage。
6. 完整测试套件必须通过；不得通过删除测试或放宽断言来获得绿色结果。

## 10. Import Boundary 自动检查

增加 AST-based test 或 lint script，不要只依赖人工 review。最低要求：

```text
magi/agent/**    不得 import magi.db, magi.tools, magi.channels
magi/tools/**    不得 import magi.db, magi.agent, magi.channels
magi/channels/** 不得 import magi.db, magi.agent, magi.tools
magi/db/**       不得 import magi.bus, magi.agent, magi.tools, magi.channels
```

允许同模块内部引用，例如 `agent -> agent.llm`、`tools.worker -> tools.registry`、`channels.delivery -> channels.telegram`。

测试需要解析 import AST，覆盖：

- `import magi.db`；
- `from magi import db`；
- `from magi.db...`；
- 动态 import 的已知调用；
- TYPE_CHECKING 中的违规依赖。

若少数迁移期例外不可避免，必须使用有到期 Phase、原因和负责人说明的 allowlist；最终验收时 allowlist 应为空。

## 11. 验收标准

以下条件全部满足才算完成：

### 架构

- Agent、Tools、Channels 之间没有直接 import 或调用。
- Agent、Tools、Channels 没有直接 DB/ORM/session/engine 访问。
- Local SQLite 与 MAGIS PostgreSQL 都封装在 BUS repository 后。
- BUS 公共 API 不返回 ORM model。
- `magi.__main__` 使用 BUS bootstrap，而不是让各模块自行初始化 DB。

### Tool Catalog

- Agent tool schemas 只来自数据库 catalog。
- Toolset/MCP/Skill 更新能够原子替换 snapshot 并增加 revision。
- Agent 不导入 `tools.registry`。
- Tool job 保存 catalog revision/schema hash。
- 工具被删除、禁用或变化时返回 durable、provider-valid 的失败结果。

### Runtime

- 同一 MAGI 的 AgentWorker 串行消费。
- Tool、A2A、Delivery 仍在事务外异步执行。
- transition 与 jobs/outbox 在 Local SQLite 中原子提交。
- steering 顺序满足 provider tool transcript 要求。
- restart 后 waiting tool/A2A run 可恢复。
- WebUI SSE 仍是 best-effort，durable result 可从 BUS 恢复。

### 测试与文档

- import-boundary test 通过且无永久 allowlist。
- 新 Actor/BUS 核心 tests 通过。
- 完整 unit/integration suite 通过。
- 文档不再把 `handle_message()` 描述为生产主路径。
- A2A、Tool hooks、delivery 和 catalog 的实现状态与代码一致。

## 12. 明确禁止事项

Codex 执行时不得：

1. 用一个更大的 `BusStore` God class 代替模块化 BUS services。
2. 让 BUS import Tool 实现、Channel adapter 或 AgentWorker。
3. 让 Agent 从 ToolWorker 的进程内 registry/cache 读取 schema。
4. 让 ORM model 穿过 BUS API。
5. 用跨 SQLite/PostgreSQL 的双 commit 冒充原子事务。
6. 将 LLM、Tool、Telegram、A2A 网络调用放进数据库事务。
7. 为了消除 import，复制相同的 DB 访问逻辑到多个模块。
8. 在工具内直接调用 channel dispatcher；必须写 BUS delivery intent。
9. 删除失败测试或削弱断言以隐藏迁移回归。
10. 在同一大 commit 中同时移动所有文件、改变 schema 和重写运行逻辑。

## 13. Codex 执行输出要求

开始修改前，Codex 应先输出：

1. 当前违规依赖清单；
2. 将新增的 BUS public API；
3. 每个 Phase 涉及的文件；
4. schema migration 计划；
5. 风险和回滚点。

每完成一个 Phase，输出：

- 修改文件；
- 删除的跨模块依赖；
- 新增/修改的 BUS contracts；
- migration 状态；
- 运行的测试及结果；
- 尚未处理的 allowlist/legacy path。

如果实际代码与本文假设不一致，应保持本文的架构约束，先报告差异，再调整具体实现；不得通过恢复直接跨模块调用来绕过问题。
