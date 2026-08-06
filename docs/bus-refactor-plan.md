# MAGI `bus` 模块重构方案

## 1. 目标

将 `magi/bus` 重构为仅有两个子包：

- **`magi/bus/db/`** — 所有数据操作内部实现，不对外暴露
- **`magi/bus/jobs/`** — Job 定义（protocols）+ 对外暴露的服务接口（services）

外部调用方只通过 `magi/bus/jobs/` 的 service 发布/领取 Job，永远看不到 ORM、SQL、数据库引擎等内部细节。

---

## 2. 现状分析

### 2.1 当前目录结构

```
magi/bus/
├── __init__.py              # 公共 API 重新导出
├── bootstrap.py             # 组合根, Bus 门面, get_bus()
├── store.py                 # BusStore (84KB) — 持久化队列操作
├── stream.py                # StreamHub — 进程内 pub/sub
├── runtime_settings.py      # 每-MAGI 运行时配置 (TOML 读写)
├── task_schedule.py         # Cron 表达式校验/人性化
│
├── protocols/               # DTO / 数据契约 (18 个文件)
│   ├── agent.py, channels.py, session.py, contact.py,
│   ├── memory.py, tools.py, magis.py, auth.py,
│   ├── action_item.py, control_jobs.py, llm_jobs.py,
│   ├── lifecycle.py, runtime.py, task.py,
│   ├── connector.py, mcp.py, common.py
│
├── services/                # 业务逻辑门面 (21 个文件)
│   ├── agent_runs.py        # → 薄包装 BusStore
│   ├── tool_jobs.py         # → 薄包装 BusStore
│   ├── delivery.py          # → 薄包装 BusStore (含一处直接 SQL)
│   ├── tool_catalog.py      # → 直接 SQLAlchemy
│   ├── session.py           # → 直接 SQLAlchemy
│   ├── contact.py           # → 直接 SQLAlchemy
│   ├── memory.py            # → 直接 SQLAlchemy (含 format_memory_block)
│   ├── task.py              # → 直接 SQLAlchemy
│   ├── magis.py             # → 直接 SQLAlchemy (MAGIS PG)
│   ├── magic.py             # → 直接 SQLAlchemy (MAGIS PG)
│   ├── auth.py              # → 混合: 直接 SQLAlchemy
│   ├── mcp.py, action_item.py, connector.py, token_usage.py
│   ├── setting.py           # → 薄包装 db/settings.py KV
│   ├── dispatcher.py        # → 无 DB: channel adapter 注册表
│   ├── control_registry.py  # → 薄包装 ControlRepository
│   ├── runtime.py           # → BackendDispatcher/RuntimeRegistry
│   └── task_scheduler_bridge.py
│
├── models/                  # SQLAlchemy ORM 表定义
│   ├── local/    (15 files) # 本地 SQLite 表
│   ├── magis/    (7 files)  # MAGIS PG 表
│   └── queue/    (10 files) # 消息总线队列表
│
├── db/                      # 持久化基础设施
│   ├── base.py, engine.py, local_db.py
│   ├── settings.py, runtime_settings.py
│   ├── alembic_runner.py, alembic/
│   ├── control/repository.py  # ControlRepository
│   └── magis/engine.py, local_engine.py
│
└── repositories/            # 目前基本为空
    ├── local_sqlite/__init__.py   (空)
    └── magis_postgres/__init__.py (空)
```

### 2.2 当前问题

1. **services 直接操作 SQLAlchemy**：`SessionService`、`ContactsService`、`MemoryService`、`TaskService`、`MagisService`、`MagicService`、`ToolCatalogService`、`AuthService` 等大量 service 内部直接 import `magi.bus.models.*` 和 `magi.bus.db.open_session`，数据访问与业务逻辑混合。

2. **repositories 目录空壳**：`repositories/local_sqlite/` 和 `repositories/magis_postgres/` 的 `__init__.py` 均为空文件，只有 `db/control/repository.py` 是真正的 repository 实现。

3. **模块职责不清晰**：`db/settings.py`（KV 操作）、`db/runtime_settings.py`（系统配置）、`bus/runtime_settings.py`（TOML 读写）三处设置相关代码分散。

4. **BusStore 位置不当**：`store.py` 是纯粹的持久化队列操作，直接 import models 和 db，本质上是数据访问层，不应该暴露在 bus 顶层。

---

## 3. 目标架构

```
magi/bus/
├── __init__.py              # 公共 API（从 jobs 重新导出）
├── bootstrap.py             # 组合根，Bus 门面，get_bus()
├── stream.py                # StreamHub 进程内事件流（非持久化）
├── task_schedule.py         # Cron 工具（纯函数，无 DB 依赖）
│
├── db/                      # 【内部】所有数据操作
│   ├── __init__.py          # 仅 bus 内部导入，不对外暴露
│   ├── base.py              # SQLAlchemy DeclarativeBase + utcnow_naive
│   ├── engine.py            # 本地 SQLite 引擎/会话工厂
│   ├── local_db.py          # SQLite 文件初始化
│   ├── settings.py          # KV 读写 (state_get/set/delete)
│   ├── runtime_settings.py  # TOML 配置读写 (← 从 bus/ 移入)
│   ├── alembic_runner.py    # 程序化 Alembic 迁移
│   ├── alembic/             # 迁移脚本
│   │
│   ├── models/              # ORM 表定义 (← 从 bus/models/ 移入)
│   │   ├── __init__.py
│   │   ├── local/           # 本地 SQLite 表
│   │   ├── magis/           # MAGIS PG 表
│   │   └── queue/           # 消息总线队列表
│   │
│   ├── store.py             # BusStore 持久化队列操作 (← 从 bus/store.py 移入)
│   │
│   ├── repositories/        # 数据访问对象 (Repository 模式)
│   │   ├── __init__.py
│   │   ├── local/           # 本地 SQLite Repository
│   │   │   ├── __init__.py
│   │   │   ├── session.py   # ChatSession/ChatMessage CRUD
│   │   │   ├── contact.py   # Contact/ContactNote CRUD
│   │   │   ├── memory.py    # MemoryEntry CRUD
│   │   │   ├── task.py      # Task/TaskRun/TaskPreset CRUD
│   │   │   ├── tool.py      # ToolCatalog CRUD
│   │   │   ├── auth.py      # 本地 credential 查询
│   │   │   ├── action_item.py
│   │   │   ├── mcp.py
│   │   │   ├── token_usage.py
│   │   │   ├── connector.py
│   │   │   └── hook.py      # Hook signoff 操作
│   │   │
│   │   └── magis/           # MAGIS Repository
│   │       ├── __init__.py
│   │       ├── magis.py     # MAGIS/MAGISRole/MAGISMembership CRUD
│   │       ├── magic.py     # MAGIC/EvaRuntime CRUD
│   │       ├── auth.py      # AuthCredential CRUD
│   │       └── control.py   # ControlRegistry (← db/control/repository.py)
│   │
│   ├── control/             # (可合并到 repositories/magis/control.py，保留过渡)
│   │   └── repository.py
│   │
│   └── magis/               # MAGIS 引擎访问
│       ├── engine.py
│       └── local_engine.py
│
└── jobs/                    # 【对外】Job 定义 + 服务接口
    ├── __init__.py           # 重新导出 protocols + services
    │
    ├── protocols/            # DTO / 数据契约 (← 从 bus/protocols/ 移入)
    │   ├── __init__.py
    │   ├── agent.py          # AgentMessage, BusClaim, RunResult, BusStoreProtocol...
    │   ├── channels.py       # Channel, InboundMessage, OutboundDelivery...
    │   ├── session.py        # Session, SessionMessage, SearchHit...
    │   ├── contact.py        # ContactView, NoteView
    │   ├── memory.py         # MemoryView
    │   ├── tools.py          # ToolClaim, ToolDefinition, ToolCatalogSnapshot...
    │   ├── magis.py          # MagisView, MagicView, MembershipBrief...
    │   ├── auth.py           # CallerIdentity
    │   ├── action_item.py    # ActionItemView
    │   ├── control_jobs.py   # ControlJobKind
    │   ├── llm_jobs.py       # LLMJob, LLMJobResult
    │   ├── lifecycle.py      # RuntimeSpec, KubernetesBackendDetail...
    │   ├── runtime.py        # BackendKind, RuntimeEndpoint
    │   ├── task.py           # TaskFullView, TaskScheduleView...
    │   ├── connector.py
    │   ├── mcp.py
    │   └── common.py
    │
    └── services/             # 对外服务门面 (← 从 bus/services/ 移入)
        ├── __init__.py
        ├── agent_runs.py     # 委托 → db/store.py (BusStore)
        ├── tool_jobs.py      # 委托 → db/store.py (BusStore)
        ├── delivery.py       # 委托 → db/store.py (BusStore)
        ├── tool_catalog.py   # 委托 → db/repositories/local/tool.py
        ├── session.py        # 委托 → db/repositories/local/session.py
        ├── contact.py        # 委托 → db/repositories/local/contact.py
        ├── memory.py         # 委托 → db/repositories/local/memory.py
        ├── task.py           # 委托 → db/repositories/local/task.py
        ├── magis.py          # 委托 → db/repositories/magis/magis.py
        ├── magic.py          # 委托 → db/repositories/magis/magic.py
        ├── auth.py           # 委托 → db/repositories/local/auth.py + magis/auth.py
        ├── setting.py        # 委托 → db/settings.py
        ├── dispatcher.py     # Channel adapter 注册表（无 DB 依赖）
        ├── control_registry.py
        ├── runtime.py
        ├── mcp.py
        ├── action_item.py
        ├── connector.py
        ├── token_usage.py
        └── task_scheduler_bridge.py
```

---

## 4. 分层职责

### 4.1 `magi/bus/db/` — 数据操作层（内部）

| 子模块 | 职责 |
|--------|------|
| `db/base.py` | SQLAlchemy `Base` 声明 + `utcnow_naive()` 工具 |
| `db/engine.py` | 本地 SQLite `Engine`、`Session` 工厂、`init_orm()`、`open_session()` |
| `db/local_db.py` | `init_sqlite()` — SQLite 文件/WAL 配置 |
| `db/settings.py` | `state_get/set/delete` — 本地 SQLite KV |
| `db/runtime_settings.py` | TOML 文件读写 — provider/api_key/model |
| `db/alembic_runner.py` | Alembic 程序化迁移 |
| `db/alembic/` | 迁移版本 |
| `db/models/` | **ORM 表定义** — 仅 db 内部导入 |
| `db/store.py` | **BusStore** — 持久化队列原子操作（claim/lease/recover/transition） |
| `db/repositories/` | **Repository 类** — 每个类封装一个领域的数据 CRUD |
| `db/magis/` | MAGIS PG 引擎访问 |
| `db/control/` | ControlRegistry repository（过渡） |

**关键原则**：
- `db/` 中的任何模块 **不被** `magi.agent`、`magi.tools`、`magi.channels` 等外部模块直接 import
- 已有 AST import-boundary 测试 (`tests/architecture/test_import_boundaries.py`) 覆盖此约束
- 仅 `bootstrap.py`（组合根）和 Alembic runner 可以 import `db/`

### 4.2 `magi/bus/jobs/` — Job 接口层（对外）

| 子模块 | 职责 |
|--------|------|
| `jobs/protocols/` | **Job 定义** — 所有 DTO/dataclass/Pydantic 模型，纯数据契约 |
| `jobs/services/` | **对外接口** — 服务门面类，暴露 `publish`/`claim`/`complete` 等方法 |

**关键原则**：
- 外部代码只通过 `bus.session.create(...)`、`bus.agent_runs.publish_input(...)` 等 service 方法交互
- service 方法接收/返回 `protocols` 中定义的 DTO，永远不泄露 ORM 对象
- service 内部委托 `db/repositories/` 或 `db/store.py` 执行实际数据操作

### 4.3 `magi/bus/` 顶层文件

| 文件 | 职责 | 归属理由 |
|------|------|----------|
| `__init__.py` | 公共 API 重新导出 | 组合根需要 |
| `bootstrap.py` | 组合根，Bus 门面，`get_bus()` | 跨层组装，必须在顶层 |
| `stream.py` | StreamHub 进程内事件流 | 非持久化，非 DB，非 Job |
| `task_schedule.py` | Cron 工具（`validate_cron`/`next_fire`/`humanize_cron`） | 纯函数，无 DB 依赖 |

---

## 5. 迁移步骤

### Phase 1：准备工作（不破坏现有代码）

1. **创建新目录结构**
   ```bash
   mkdir -p magi/bus/db/models/{local,magis,queue}
   mkdir -p magi/bus/db/repositories/{local,magis}
   mkdir -p magi/bus/jobs/protocols
   mkdir -p magi/bus/jobs/services
   ```

2. **处理 `repositories/` 空壳**
   - 当前 `magi/bus/repositories/local_sqlite/__init__.py` 和 `magi/bus/repositories/magis_postgres/__init__.py` 为空文件
   - 直接删除 `magi/bus/repositories/` 目录（无任何代码引用）

### Phase 2：移动 models（最低风险）

3. **移动 ORM 模型到 `db/models/`**
   - `magi/bus/models/*` → `magi/bus/db/models/*`
   - 代码中所有 `from magi.bus.models.xxx import YYY` → `from magi.bus.db.models.xxx import YYY`
   - 影响范围：`store.py`、`services/*.py`、`db/engine.py`、`db/control/repository.py`

### Phase 3：移动 db 基础设施

4. **合并 `db/` 内容**
   - `magi/bus/db/*` 已在目标位置，无需移动
   - `magi/bus/runtime_settings.py` → `magi/bus/db/runtime_settings.py`
     - 更新 `services/magic.py` 中的 import

5. **移动 `store.py` 到 `db/`**
   - `magi/bus/store.py` → `magi/bus/db/store.py`
   - 更新 `services/agent_runs.py`、`services/tool_jobs.py`、`services/delivery.py` 的 import
   - 更新 `bootstrap.py` 的 import
   - 更新 `__init__.py` 的重新导出

### Phase 4：移动 protocols + services 到 jobs/

6. **移动 protocols**
   - `magi/bus/protocols/*` → `magi/bus/jobs/protocols/*`
   - 更新所有 `from magi.bus.protocols.xxx import YYY` → `from magi.bus.jobs.protocols.xxx import YYY`
   - 影响范围：`services/*.py`、`store.py`、`__init__.py`、`bootstrap.py`，以及 `magi/` 下所有外部 import

7. **移动 services**
   - `magi/bus/services/*` → `magi/bus/jobs/services/*`
   - 更新 `bootstrap.py`、`__init__.py` 的 import
   - 更新 `magi/` 下所有外部 import（`get_bus().session` 等通过 Bus 门面访问的不受影响）

### Phase 5：提取 Repository 层（核心重构）

这是最关键的一步。当前 services 中直接操作 SQLAlchemy 的逻辑需要提取到 Repository。

8. **创建 Repository 类**

   以 `SessionService` 为例：

   **Before** (`jobs/services/session.py`):
   ```python
   class SessionService:
       def __init__(self, state_dir: str) -> None:
           self._state_dir = state_dir
   
       def create(self, uid: int, *, channel: str, ...) -> Session:
           from magi.bus.db.models.local.session import ChatSession
           from magi.bus.db import open_session
           with open_session(self._state_dir) as db:
               db.add(ChatSession(...))
               db.commit()
           return Session(...)
   ```

   **After**:
   
   `db/repositories/local/session.py`:
   ```python
   class SessionRepository:
       """ChatSession / ChatMessage CRUD (internal, only imported by SessionService)."""
       
       def __init__(self, state_dir: str) -> None:
           self._state_dir = state_dir
       
       def create(self, uid: int, *, channel: str, ...) -> ChatSession:
           from magi.bus.db.models.local.session import ChatSession
           from magi.bus.db import open_session
           with open_session(self._state_dir) as db:
               db.add(ChatSession(...))
               db.commit()
               return row  # 返回 ORM 对象，由 service 转为 DTO
   ```

   `jobs/services/session.py`:
   ```python
   class SessionService:
       def __init__(self, repo: SessionRepository) -> None:
           self._repo = repo
       
       def create(self, uid: int, *, channel: str, ...) -> Session:
           row = self._repo.create(uid, channel=channel, ...)
           return self._to_dto(row)  # ORM → DTO 转换
   ```

9. **各 Repository 提取计划**

   | Service | Repository | 提取内容 |
   |---------|-----------|---------|
   | SessionService | `db/repositories/local/session.py` | ChatSession/ChatMessage CRUD, FTS 搜索 |
   | ContactsService | `db/repositories/local/contact.py` | Contact/ContactNote CRUD, 搜索, 绑定 |
   | MemoryService | `db/repositories/local/memory.py` | MemoryEntry CRUD, `format_memory_block` 留在 service |
   | TaskService | `db/repositories/local/task.py` | Task/TaskRun/TaskPreset CRUD, seed_presets |
   | ToolCatalogService | `db/repositories/local/tool.py` | ToolCatalogState/ToolDefinitionRecord CRUD |
   | MagisService | `db/repositories/magis/magis.py` | MAGIS/MAGISRole/MAGISMembership CRUD |
   | MagicService | `db/repositories/magis/magic.py` | MAGIC/EvaRuntime CRUD |
   | AuthService | `db/repositories/local/auth.py` + `magis/auth.py` | Contact role 查询、AuthCredential CRUD |
   | ActionItemService | `db/repositories/local/action_item.py` | ActionItem CRUD |
   | McpService | `db/repositories/local/mcp.py` | MCP server 配置 CRUD |
   | TokenUsageService | `db/repositories/local/token_usage.py` | TokenUsage CRUD |
   | ConnectorService | `db/repositories/local/connector.py` | Connector 配置 CRUD |
   | SettingsService | `db/settings.py`（已存在） | 薄包装，不需额外 repo |
   | AgentRunsService | `db/store.py`（已存在） | 薄包装 BusStore，不需额外 repo |
   | ToolJobsService | `db/store.py`（已存在） | 薄包装 BusStore，不需额外 repo |
   | DeliveryService | `db/store.py`（已存在） | 薄包装 BusStore，不需额外 repo |
   | DispatcherService | 无 DB 依赖 | 保持原样 |

10. **更新 `bootstrap.py` 组合根**

    ```python
    def _bootstrap(state_dir: str, ...) -> Bus:
        # 创建 Repository 实例
        session_repo = SessionRepository(state_dir)
        contact_repo = ContactRepository(state_dir)
        memory_repo = MemoryRepository(state_dir)
        # ...
        
        # 创建 Store
        store = BusStore(state_dir)
        
        # 组装 Bus
        bus = Bus(
            session=SessionService(session_repo),
            contacts=ContactsService(contact_repo),
            memory=MemoryService(memory_repo),
            agent_runs=AgentRunsService(store),
            tool_jobs=ToolJobsService(store),
            delivery=DeliveryService(store),
            # ...
        )
    ```

### Phase 6：更新公共 API

11. **更新 `magi/bus/__init__.py`**
    - 所有重新导出改为从 `magi.bus.jobs` 获取
    - `Bus`、`BusStore`、`BusStoreProtocol` 等保持导出路径兼容
    - `bootstrap`、`get_bus`、`get_bus_store` 保持位置不变

12. **更新 `magi/bus/jobs/__init__.py`**
    - 重新导出所有 protocols DTO
    - 重新导出所有 service 类

### Phase 7：全项目 import 更新

13. **搜索并替换所有外部 import**
    ```bash
    # 搜索模式
    from magi.bus.protocols.xxx import YYY
    from magi.bus.services.xxx import YYY
    from magi.bus.models.xxx import YYY
    from magi.bus.db.xxx import YYY  # 外部模块不应直接导入 db
    from magi.bus.store import BusStore
    
    # 替换为
    from magi.bus.jobs.protocols.xxx import YYY
    from magi.bus.jobs.services.xxx import YYY
    # models/db 的外部导入应消除（改为通过 service）
    from magi.bus import BusStore  # 或消除直接依赖
    ```

14. **更新 import-boundary 测试**
    - `tests/architecture/test_import_boundaries.py`
    - 禁止外部模块 import `magi.bus.db.*`
    - 允许外部模块 import `magi.bus.jobs.protocols.*` 和 `magi.bus.jobs.services.*`
    - `magi.bus.bootstrap` 是唯一允许 import `magi.bus.db.*` 的模块

### Phase 8：清理

15. **删除旧目录**
    - `magi/bus/protocols/` → 已迁移到 `jobs/protocols/`
    - `magi/bus/services/` → 已迁移到 `jobs/services/`
    - `magi/bus/models/` → 已迁移到 `db/models/`
    - `magi/bus/repositories/` → 已迁移到 `db/repositories/`
    - `magi/bus/store.py` → 已迁移到 `db/store.py`
    - `magi/bus/runtime_settings.py` → 已迁移到 `db/runtime_settings.py`

---

## 6. 关键设计决策

### 6.1 Repository 返回什么？

**方案 A**：Repository 返回 ORM 对象，Service 做 ORM→DTO 转换
- 优点：Repository 职责单一，不依赖 protocols
- 缺点：ORM 对象短暂暴露给 Service 层

**方案 B**：Repository 直接返回 DTO
- 优点：ORM 完全封装在 db/ 内
- 缺点：Repository 需要 import protocols，造成 `db/` → `jobs/` 的依赖

**建议：方案 A**。Repository 在 `db/` 内不应依赖 `jobs/`。Service 做 ORM→DTO 转换是合理的（本来就是 service 的职责），且 ORM 对象通过 `with` 块限定生命周期。

### 6.2 BusStore 是否仍然公开导出？

**当前**：`BusStore` 在 `__init__.py` 中作为公共 API 导出，被 `get_bus_store()` 返回。

**建议**：`BusStore` 移入 `db/` 后，仅作为内部实现。但 `bootstrap.py` 中 `Bus` 门面的 `store` 字段仍可保留（供 plugin worker 等内部使用）。外部新增代码应使用 service，不应直接调 `bus.store.xxx()`。

过渡期保留 `get_bus_store()` 向后兼容，标记为 deprecated。

### 6.3 `format_memory_block` 放哪里？

这是一个纯格式化函数，输入 `MemoryView` DTO，输出 Markdown 字符串。不涉及任何 DB 操作。

**建议**：保留在 `jobs/services/memory.py`（作为 MemoryService 的模块级函数）。这是"应用层读取端格式化器"，属于 service 层。

### 6.4 `stream.py` 放哪里？

StreamHub 是进程内 pub/sub，不持久化。它服务于 agent loop 的 SSE streaming 场景。

**建议**：保留在 `magi/bus/stream.py` 顶层。它不是 DB 操作，也不是典型的 Job 定义/接口模式。它与 `bootstrap.py` 同为跨切面基础设施。

### 6.5 `task_schedule.py` 放哪里？

Cron 表达式校验、人性化展示、预设构建器。纯函数，无 DB 依赖。

**建议**：保留在 `magi/bus/task_schedule.py` 顶层。它是工具函数，被 `TaskService` 和 `task_scheduler_bridge` 使用。

---

## 7. 外部影响范围

### 7.1 通过 Bus 门面访问的代码（无变化）

以下模式不受影响，因为调用方通过 `bus.xxx` 属性访问：

```python
bus = get_bus()
bus.session.create(...)
bus.agent_runs.publish_input(...)
bus.memory.add(...)
```

### 7.2 直接 import protocols 的代码（需更新）

```python
# 旧
from magi.bus.protocols.agent import AgentMessage, BusClaim

# 新
from magi.bus.jobs.protocols.agent import AgentMessage, BusClaim
# 或通过顶层
from magi.bus import AgentMessage, BusClaim
```

受影响的模块（非完整列表）：
- `magi/agent/loop.py`
- `magi/agent/worker.py`
- `magi/tools/*.py`
- `magi/channels/*.py`
- `magi/orchestrator/*.py`
- `magi/proactive/*.py`
- `magi/connectors/*.py`
- `magi/mcp/*.py`
- `magi/plugins/*.py`

### 7.3 直接 import services/models/db 的代码（需消除）

这些是当前不规范的用法，应在本次重构中消除：

```python
# 应消除的模式
from magi.bus.models.local.session import ChatSession
from magi.bus.db import open_session
```

改为通过 bus service 访问。

---

## 8. 测试策略

1. **import-boundary 测试**（`tests/architecture/test_import_boundaries.py`）
   - 更新规则：禁止外部模块 import `magi.bus.db.*`
   - 验证 `magi.bus.jobs` 的内部代码不 import `magi.bus.db.models.*`（service 不应直接操作 ORM）

2. **单元测试**
   - Repository 测试：直接测试 CRUD 操作
   - Service 测试：mock Repository，测试业务逻辑和 DTO 转换

3. **集成测试**
   - 端到端测试 Bus 门面的所有 service 方法
   - 覆盖现有的 queue 操作（claim/lease/recover）

4. **回归测试**
   - 运行全量测试套件
   - 确保 Worker 架构（AgentWorker → ToolWorker → DeliveryWorker）正常工作

---

## 9. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 大量 import 路径变更 | 编译错误，运行时 ImportError | Phase 逐步推进，每步验证 |
| Repository 提取引入 bug | 数据不一致 | 保持现有 SQL 逻辑不变，仅移动位置 |
| BusStore 移入 db/ 影响外部 | 直接 import BusStore 的代码失效 | 顶层 `__init__.py` 保持重新导出，标记 deprecated |
| Protocol DTO 位置变化 | 跨模块序列化/类型检查失效 | 更新所有 import，利用 IDE/类型检查器验证 |

---

## 10. 时间估算

| Phase | 内容 | 预计工作量 |
|-------|------|-----------|
| Phase 1 | 创建目录结构 | 0.5h |
| Phase 2 | 移动 models | 1h |
| Phase 3 | 移动 db 基础设施 | 1h |
| Phase 4 | 移动 protocols + services | 2h |
| Phase 5 | 提取 Repository 层 | 4-6h |
| Phase 6 | 更新公共 API | 1h |
| Phase 7 | 全项目 import 更新 | 2-3h |
| Phase 8 | 清理 + 测试 | 2h |
| **总计** | | **14-17h** |

---

## 11. 附录：BusStore 方法分类

`store.py` 中的 `BusStore` 方法按职责分组，帮助理解哪些应保留在 Store 中，哪些应提取到 Repository：

### 队列操作（保留在 `db/store.py`）

| 方法 | 职责 |
|------|------|
| `publish_agent_message()` | 发布 agent 输入消息 |
| `claim_next_agent_message()` | 领取下一条 agent 消息 |
| `commit_agent_transition()` | 提交 agent 状态转换 |
| `fail_agent_message()` | 标记 agent 消息失败 |
| `get_run_result()` | 获取 run 结果 |
| `recover_expired_leases()` | 恢复过期租约 |
| `cancel_run()` | 取消 run |
| `publish_tool_job()` | 发布工具执行 job |
| `claim_next_tool_job()` | 领取下一条工具 job |
| `complete_tool_job()` | 完成工具 job |
| `retry_tool_job()` | 重试工具 job |
| `enqueue_delivery()` | 入队投递 |
| `claim_next_delivery()` | 领取下一条投递 |
| `complete_delivery()` | 完成投递 |
| `retry_delivery()` | 重试投递 |
| `complete_a2a_invocation()` | 完成 A2A 调用 |
| `expire_a2a_invocations()` | 过期 A2A 调用 |
| `publish_control_job()` | 发布控制 job |

### 领域 CRUD（提取到 `db/repositories/`）

| 方法 | 目标 Repository |
|------|----------------|
| 各种 `_dispatch_hook_signoffs()` | `db/repositories/local/hook.py` |
| (Store 中不直接包含 CRUD，但 service 中包含) | 见 Phase 5 表格 |

---

*文档版本: v1.0 | 日期: 2026-08-05*
