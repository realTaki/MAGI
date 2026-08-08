# MCP Worker 设计文档

## 概述

将 `magi/mcp/` 模块改造为一个独立的 **MCP Worker**，遵循与 `ProvidersWorker`、`ToolsWorker` 相同的 Worker 架构模式。MCP Worker 负责：

1. **启动时**：从 `new_bus.mcp_servers_book` 读取已启用的 MCP 服务器，建立连接，发现 tools，注入到 `magi.tools.registry`。
2. **运行时**：监听 MCP 服务器变更 Job（新增/修改/删除/启禁），重新连接/断开，更新 registry。

## 当前状态分析

### 现有架构问题

```
当前流程（无 Worker）:
  API 端点 / LLM manage tool
    → old bus McpService.upsert/delete/toggle
    → 直接写 mcp_servers 表
    → 下次 chat turn 时 maybe_reload_mcp_tools 检测 updated_at 变更
    → loader.load_mcp_tools_async() 重新连接全部服务器
    → 但工具并未注入 registry.register_tools()！
```

关键发现：
- **MCP 工具（manage tools + discovered tools）目前并未通过 `register_tools()` 注入 registry**。文档和注释中描述了这一设计意图，但代码尚未实现。
- 当前 MCP 工具存在于独立的 `mcp/loader.py` 模块级缓存 `_connections` 中，与 tools registry 是分离的。
- `ToolsWorker._publish_full_catalog()` 遍历 `list_injected()` 时找不到 MCP 工具（因为未注入）。
- `mcp_servers_book`（new_bus）与 old bus 的 `McpServer` ORM 使用**不同 schema 却指向同一张表**，存在列定义冲突风险。

### 两套 MCP 数据模型对比

| 字段 | old bus `McpServer` ORM | new_bus `_McpServerRow` |
|------|------------------------|------------------------|
| 主键 | `name` (String(64)) | `id` (autoincrement int) |
| 类型 | `connection_type` | `transport` |
| 命令 | `command` | 在 `config` dict 中 |
| 参数 | `args_json` (Text/JSON) | 在 `config` dict 中 |
| 环境变量 | `env_json` (Text/JSON) | 在 `config` dict 中 |
| URL | `url` | 在 `config` dict 中 |
| Headers | `headers_json` (Text/JSON) | 在 `config` dict 中 |
| 超时 | 三个独立 Float 列 | 在 `config` dict 中 |
| 启禁 | `enabled` (Boolean) | `enabled` (Integer 0/1) |

**决策点**: 需要统一 schema，或在 worker 中桥接两套模型。

## 设计方案

### 1. 数据层：统一 MCP Server Book

#### 方案 A（推荐）：扩展 new_bus McpServerBook 到完整 schema

将 `new_bus/library/local/mcpServerBook.py` 的 `_McpServerRow` 和 `McpServer` DTO 扩展为与 old bus 一致的完整 schema。old bus 的 `mcp_servers` 表保持不变，new_bus 的 ORM 映射到同一张表但使用完整列定义。

```python
# new_bus/library/local/mcpServerBook.py (扩展后)

class _McpServerRow(Base):
    __tablename__ = "mcp_servers"
    __table_args__ = {"extend_existing": True}

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    connection_type: Mapped[str] = mapped_column(String(16), nullable=False)
    command: Mapped[str | None] = mapped_column(String(256), nullable=True)
    args_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    env_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    headers_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    connect_timeout: Mapped[float | None] = mapped_column(Float, nullable=True)
    execute_timeout: Mapped[float | None] = mapped_column(Float, nullable=True)
    sse_read_timeout: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

@dataclass(frozen=True, slots=True)
class McpServer:
    name: str
    connection_type: str  # "stdio" | "sse" | "streamable_http"
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    connect_timeout: float | None = None
    execute_timeout: float | None = None
    sse_read_timeout: float | None = None
    created_at: str | None = None
    updated_at: str | None = None
```

**注意**：需要 `__table_args__ = {"extend_existing": True}` 以避免与 old bus 的 ORM 定义冲突。同时需要在 `_row_to_dto` 中处理 JSON 字段的反序列化（`args_json` → `tuple`, `env_json` → `dict`, `headers_json` → `dict`）。

#### McpServerBook 新增方法

```python
class McpServerBook(BaseBook[_McpServerRow, McpServer]):
    # 现有: get, get_by_name, list_all, list_enabled, add, update, delete

    def list_enabled(self) -> list[McpServer]: ...
    def get_by_name(self, *, name: str) -> McpServer | None: ...
    def upsert(self, *, name: str, ...) -> McpServer: ...
    def delete_by_name(self, *, name: str) -> bool: ...
    def toggle(self, *, name: str) -> McpServer | None: ...
```

### 2. Job 层：MCP Server 变更通知

#### 新增 `mcpServerChangedJob.py`

仿照 `controlJob.py` 和 `changeProviderConfigJob.py` 的模式，创建一个 MCP 服务器变更的 Job Board。

```python
# new_bus/guild/mcpServerChangedJob.py

@dataclass(frozen=True, slots=True)
class McpServerChangedJob:
    """一个 MCP 服务器发生了变更。"""
    kind: str  # "added" | "updated" | "deleted" | "toggled"
    server_name: str
    job_id: str = ""

@dataclass(frozen=True, slots=True)
class McpServerChangedResult:
    job_id: str
    success: bool
    error: str | None = None

class _McpServerChangedRow(Base):
    __tablename__ = "mcp_server_changed_jobs"
    __table_args__ = {"extend_existing": True}
    # ... 标准 job 列 (job_id, status, kind, payload, leased_until, attempts, result, error, ...)

class mcpServerChangedJobBoard(BaseJobBoard[_McpServerChangedRow, McpServerChangedJob, McpServerChangedResult]):
    job_model = _McpServerChangedRow
    job_cls = McpServerChangedJob
    result_cls = McpServerChangedResult
```

**谁 publish Job？（未来集成点，当前不改）**

以下三个入口未来需要 publish Job，但**当前阶段不改动**——这些模块尚未迁移到 new_bus，只加注释标记：

1. **WebUI API** (`magi/channels/api/mcp_servers.py`) — 不改，加 `# TODO(mcp-worker): publish McpServerChangedJob after writing`
2. **LLM Manage Tools** (`magi/mcp/manage.py`) — 不改，加 `# TODO(mcp-worker): publish McpServerChangedJob after writing`
3. 两个模块仍然直写 old bus `McpService`，Worker 暂时只做启动时 bootstrap，运行时不感知变更。

### 3. Worker 层：MCP Worker

#### 文件：`magi/mcp/worker.py`

```python
class McpWorker:
    """消费 MCP 服务器配置变更，管理 MCP 连接，注入工具到 registry。

    启动时：
    1. 从 mcp_servers_book 读取所有 enabled 服务器
    2. 并行连接每个服务器
    3. 将发现工具 + 管理工具注入 registry

    运行时：
    1. 轮询 mcp_server_changed_job_board
    2. 根据变更类型：重连/断开/更新特定服务器
    3. 重新注入工具到 registry
    """

    def __init__(self, bus: NewBus, *, poll_seconds: float = 0.25):
        self.bus = bus
        self.poll_seconds = poll_seconds
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._connections: dict[str, MCPServerConnection] = {}  # name → connection

    async def start(self) -> None:
        """启动时引导：加载服务器 → 连接 → 注入工具。"""
        # 1. 注入 MCP 管理工具（始终可用）
        manage_tools = [AddMcpServerTool(), ListMcpServersTool(),
                        UpdateMcpServerTool(), DeleteMcpServerTool()]
        register_tools("mcp_manage", manage_tools)

        # 2. 从 Book 读取 enabled 服务器并连接
        await self._bootstrap_connections()

        # 3. 启动主循环
        self._task = asyncio.create_task(self._run(), name="magi-mcp-worker")

    async def stop(self) -> None:
        """优雅关闭：断开所有 MCP 连接，清理。"""
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        await self._disconnect_all()
        register_tools("mcp", [])  # 清除 MCP 工具

    async def _run(self) -> None:
        """主循环：轮询 MCP 变更 Job。"""
        while not self._stopping:
            try:
                job = await asyncio.to_thread(
                    self.bus.mcp_server_changed_job_board.claim
                )
            except Exception:
                logger.exception("mcp worker: claim failed")
                await asyncio.sleep(self.poll_seconds)
                continue

            if job is None:
                await asyncio.sleep(self.poll_seconds)
                continue

            await self._handle_change(job)

    async def _handle_change(self, job: McpServerChangedJob) -> None:
        """处理单个 MCP 服务器变更。"""
        ...
```

#### 核心方法

```python
async def _bootstrap_connections(self) -> None:
    """启动时：连接所有 enabled MCP 服务器，注入发现工具。"""
    servers = self.bus.mcp_servers_book.list_enabled()
    if not servers:
        register_tools("mcp", [])
        return

    timeouts = _defaults_from_bus(self.bus)
    connections: dict[str, MCPServerConnection] = {}

    # 并行连接
    async def _connect_one(srv: McpServer) -> tuple[str, MCPServerConnection | None]:
        conn = MCPServerConnection(
            name=srv.name,
            connection_type=srv.connection_type,
            command=srv.command, args=list(srv.args), env=srv.env,
            url=srv.url, headers=srv.headers,
            connect_timeout=srv.connect_timeout,
            execute_timeout=srv.execute_timeout,
            sse_read_timeout=srv.sse_read_timeout,
        )
        ok = await conn.connect(timeouts)
        return (srv.name, conn if ok else None)

    results = await asyncio.gather(*(_connect_one(s) for s in servers))
    for name, conn in results:
        if conn:
            connections[name] = conn

    self._connections = connections
    self._reinject_tools()
    logger.info("mcp worker: bootstrapped %d/%d servers, %d tools total",
                len(connections), len(servers), ...)

async def _handle_change(self, job: McpServerChangedJob) -> None:
    """处理 MCP 服务器变更。"""
    name = job.server_name
    success = False
    error: str | None = None

    try:
        if job.kind == "deleted":
            await self._remove_server(name)
            success = True
        elif job.kind in ("added", "updated", "toggled"):
            await self._reload_server(name)
            success = True
        else:
            error = f"unknown change kind: {job.kind}"
    except Exception as e:
        logger.exception("mcp worker: failed to handle change for %r", name)
        error = str(e)

    try:
        self.bus.mcp_server_changed_job_board.submit_result(
            key=job.job_id,
            result=McpServerChangedResult(
                job_id=job.job_id, success=success, error=error,
            ),
        )
    except Exception:
        logger.exception("mcp worker: failed to submit change result")

def _reinject_tools(self) -> None:
    """从当前连接重新构建工具列表并注入 registry。"""
    all_tools: list[MCPTool] = []
    for conn in self._connections.values():
        all_tools.extend(conn.tools)
    register_tools("mcp", all_tools)
    # register_tools 内部会触发 on_tools_changed → ToolsWorker._on_injected_tools_changed
    # → ToolsWorker 自动重新发布 tool catalog
```

### 4. 启动集成

在 `magi/startup/runtime.py` 的 `_runtime_lifespan()` 中加入 MCP Worker：

```python
async def _runtime_lifespan(workers, channels, new_bus, *, magi_id=None):
    ...
    await start_provider_worker(bus=new_bus)
    await start_tool_worker(bus=new_bus)
    await start_mcp_worker(bus=new_bus)      # ← 新增：MCP Worker
    await start_agent_worker()
    await start_delivery_worker()
    await start_proactive_worker(bus=new_bus, magi_id=magi_id)
    try:
        yield
    finally:
        await stop_proactive_worker()
        await stop_delivery_worker()
        await stop_agent_worker()
        await stop_mcp_worker()               # ← 新增
        await stop_tool_worker()
        await stop_provider_worker()
```

**启动顺序理由**：MCP Worker 在 Tools Worker 之后启动。Tools Worker 先发布 builtin catalog，MCP Worker 再连接 MCP 服务器并注入 MCP 工具。Tools Worker 通过 `on_tools_changed` 监听器自动检测到新工具并重新发布 catalog。

在 `worker_lifespan()` 中同理。

### 5. 与现有代码的关系

| 模块 | 变更 |
|------|------|
| `magi/mcp/loader.py` | **保留**。`MCPServerConnection`、`MCPTool`、`MCPTimeoutConfig` 等类保持不变，作为 MCP Worker 的底层工具库。移除模块级 `_connections` 全局缓存（由 Worker 管理）。移除 `load_mcp_tools_async`/`load_mcp_tools_blocking`（不再需要外部调用）。 |
| `magi/mcp/manage.py` | **不改**。加 `# TODO(mcp-worker): publish McpServerChangedJob after writing` 注释。四个 LLM CRUD 工具保持不变。 |
| `magi/mcp/__init__.py` | 新增导出 `McpWorker`、`start_mcp_worker`、`stop_mcp_worker`。 |
| `magi/mcp/worker.py` | **新建**。MCP Worker 主体。 |
| `magi/tools/registry.py` | **不变**。MCP Worker 通过 `register_tools("mcp", ...)` 和 `register_tools("mcp_manage", ...)` 注入。 |
| `magi/tools/worker.py` | **不变**。通过 `on_tools_changed` 自动响应 MCP 工具变更。 |
| `magi/channels/api/mcp_servers.py` | **不改**。加 `# TODO(mcp-worker): publish McpServerChangedJob after writing` 注释。迁移报错不管。 |
| `magi/new_bus/library/local/mcpServerBook.py` | **扩展**。Schema 扩展为完整字段，新增 `upsert`/`delete_by_name`/`toggle` 方法。 |
| `magi/new_bus/guild/mcpServerChangedJob.py` | **新建**。Job Board 定义（当前无调用方，Worker 轮询空转，为未来预留）。 |
| `magi/new_bus/bootstrap.py` | **修改**。`NewBus` dataclass 新增 `mcp_server_changed_job_board` 字段，`_bootstrap_with_dirs` 中初始化。 |
| `magi/startup/runtime.py` | **修改**。`_runtime_lifespan` 和 `worker_lifespan` 中加入 MCP Worker 启动/停止。 |
| `magi/bus/jobs/services/mcp.py` | **不改**。仍被 API/旧模块使用。最终可废弃。 |

### 6. 数据流总结

**当前阶段（启动时 bootstrap，运行时 Job Board 空转等待）：**

```
启动时:
  McpWorker.start()
    ├─ register_tools("mcp_manage", [AddMcpServerTool, ...])
    ├─ mcp_servers_book.list_enabled()
    ├─ 并行连接每个 MCP 服务器
    ├─ register_tools("mcp", discovered_tools)
    │    │
    │    ▼
    │  on_tools_changed → ToolsWorker 自动重发布 catalog
    └─ spawn _run() loop (轮询 mcpServerChangedJobBoard，当前无 Job)

运行时（未来集成后）:
  操作者 (WebUI / LLM)
    │
    ├─ 写 mcp_servers_book
    └─ publish McpServerChangedJob  ← 当前未实现，TODO
         │
         ▼
    mcpServerChangedJobBoard (SQLite)
         │
         ▼
    McpWorker._run() → claim → 重连/断开 → re-inject tools
```

### 7. 待确认的设计决策

1. **Book schema 统一方式**：方案 A（扩展 new_bus McpServerBook 到完整 schema）+ `extend_existing=True`。需要确认这与现有 old bus ORM 定义无冲突。

2. **当前阶段不改调用方**：API 端点 (`mcp_servers.py`) 和 LLM manage tools (`manage.py`) 暂不改动，只加 `# TODO(mcp-worker)` 注释。这两个模块仍直写 old bus，迁移导致报错暂不管。Worker 启动时从 Book bootstrap 连接；运行时 Job Board 已就位，待这些模块迁移后接入。

3. **`McpServerChangedJob` 的表**：需要新建 `mcp_server_changed_jobs` 表。与现有的 `control_jobs` 表设计类似但语义独立（control 是系统级信号，MCP 变更是领域事件）。

4. **超时配置**：MCP 超时配置从 `settings_book` 读取（`mcp.connect_timeout` 等），由 MCP Worker 自行读取，不通过构造函数注入（因为可能在运行时变更）。

5. **manage tools 注册时机**：MCP 管理工具（add/list/update/delete）在 MCP Worker 启动时注册为 `"mcp_manage"` source。即使没有配置任何 MCP 服务器，这些管理工具也应该可用（admin 需要先创建服务器才能发现工具）。

6. **错误恢复**：单个 MCP 服务器连接失败不应阻塞其他服务器的引导。如果某台服务器连接失败，Worker 记录错误日志但继续。后续收到该服务器的 "updated" Job 时可重试连接。
