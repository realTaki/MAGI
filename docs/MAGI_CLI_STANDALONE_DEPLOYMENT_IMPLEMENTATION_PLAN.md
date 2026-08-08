# MAGI CLI 单机部署：实现对照与加固指南

> **文档状态：非权威 / 历史设计记录**
>
> 本文基于 2026-08-08 的代码快照整理。它不定义当前 CLI 的最终行为，也不替代代码、部署脚本或权威架构文档。
> 本文的用途是：
>
> 1. 记录单机 CLI 部署已经采用的设计原则；
> 2. 标出当前实现与原设计之间仍存在的差异；
> 3. 保留值得继续落实的可靠性、安全性、数据迁移和发布要求。
>
> **行为以代码为准**。当前实现主要位于 [`magi/startup/`](../magi/startup/)、[`magi/bus/`](../magi/bus/)、[`magi/channels/api/`](../magi/channels/api/) 和 [`deploy/cli/`](../deploy/cli/)。
>
> **架构边界以以下文档为准**：
>
> - [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) — 总体架构与统一启动结构；
> - [`docs/MAGI_BUS_CENTRIC_ARCHITECTURE.md`](MAGI_BUS_CENTRIC_ARCHITECTURE.md) — BUS、Actor、持久化和模块边界；
> - [`docs/MAGI_MODULE_RESPONSIBILITIES_AND_DEPENDENCIES.md`](MAGI_MODULE_RESPONSIBILITIES_AND_DEPENDENCIES.md) — 模块职责；
> - [`deploy/README.md`](../deploy/README.md) — 三种部署方式概览。
>
> 本文完成加固项迁移并更新相关引用后，可以删除；在此之前不应作为“已完成实现”的说明使用。

---

## 1. 范围与产品定位

CLI Profile 是 MAGI 的**单机、非容器、可信单用户**部署方式，适用于：

- 本地开发和评审；
- 研究、benchmark 和可复现实验；
- 单机 PoC 和小规模自托管。

它的基本目标是：

```text
一个 MAGI = 一个独立 OS 进程
一个 MAGI = 一个私有 Workspace + 一个私有 SQLite
一个 MAGIS = 一个组织数据库 + 一个共享 WebUI
```

CLI Profile **不是** Kubernetes 的生产替代品，也不是安全 sandbox。Workspace 分目录和独立进程只能提供组织与故障边界，不能提供容器、Namespace、RBAC 或 PVC 级别的隔离。

当前 Runtime 组合和 Worker 生命周期由 [`magi/startup/runtime.py`](../magi/startup/runtime.py) 负责；本地进程生命周期由 [`magi/startup/local.py`](../magi/startup/local.py) 负责；WebUI 生命周期由 [`magi/startup/webui.py`](../magi/startup/webui.py) 负责。

---

## 2. 当前实现快照

### 2.1 启动代码布局

当前启动代码统一收敛到 [`magi/startup/`](../magi/startup/)：

| 模块 | 当前职责 |
| --- | --- |
| `config.py` | `StartupConfig`、四个启动输入、校验、Workspace 推导 |
| `paths.py` | Host、MAGI、MAGIS、PID、日志和运行状态路径辅助函数 |
| `bootstrap.py` | 首个 MAGIS/MAGI 初始化、加入已有 MAGIS、私有 DB 和运行状态 |
| `runtime.py` | 单个 MAGI 的 BUS、Worker、Channel 和 Runtime API 组合 |
| `local.py` | 本地 MAGI 创建、启动、停止、重启、状态查询和子进程日志 |
| `webui.py` | 每个 MAGIS 的 singleton WebUI 进程生命周期 |
| `kubernetes/` | Kubernetes manifest、资源客户端和控制服务 |
| `cli.py` | 启动命令的 argparse handler |

旧的 `magi.launcher`、独立的 CLI Backend 和第二套 Runtime 组合不应重新引入。启动层可以装配所有模块，但不承载 Agent、Tool 或 Channel 业务逻辑。

### 2.2 当前进程模型

当前本地启动使用 detached subprocess，而不是 supervisor 树或 `execve` 替换：

```text
magi cli start
    ↓
startup.local.start_magi()
    ↓
subprocess.Popen(start_new_session=True)
    ↓
magi runtime 子进程
```

每个 MAGI 进程拥有自己的 Workspace、日志和 PID 文件。当前实现没有常驻 Local Orchestrator，也没有统一的多 MAGI supervisor。systemd 如果将来支持，应当仍然按 MAGI 注册独立 unit，而不是重新构造一套业务后端。

### 2.3 当前 WebUI 模型

一个 MAGIS 只有一个 WebUI：

```text
MAGIS
├── MAGI Runtime 1
├── MAGI Runtime 2
└── singleton WebUI
```

WebUI 产品代码位于 [`magi/channels/api/app.py`](../magi/channels/api/app.py)。[`magi/startup/webui.py`](../magi/startup/webui.py) 只负责启动、停止、PID、日志和恢复，不应复制一套 WebUI 业务实现。

WebUI 通过 BUS registry 查询 Runtime endpoint，再使用短期 HMAC 请求访问被选中的 Runtime。浏览器不能直接提交一个任意上游 URL。

### 2.4 当前启动契约

代码中的启动配置由四个输入组成：

```text
HOST_WORKSPACE_DIR
MAGI_NAME
MAGIS_DATABASE_URL
MAGI_ID
```

含义如下：

| 输入 | 作用 |
| --- | --- |
| `HOST_WORKSPACE_DIR` | 操作员持久化数据根目录；默认由启动配置解析 |
| `MAGI_NAME` | MAGI 名称，同时参与 Workspace 推导；首个 MAGI 默认 `eva-000` |
| `MAGIS_DATABASE_URL` | 已有 MAGIS 的 DSN；缺失时表示执行首个 MAGIS bootstrap |
| `MAGI_ID` | 加入已有 MAGIS 时的持久身份；不能用 PID、Pod 名或显示名称替代 |

最终 Workspace 应由 `HOST_WORKSPACE_DIR + MAGI_NAME` 推导，而不是由调用方直接传入最终路径。这个原则保留，但当前路径 helper、Runtime state 路径和部署脚本仍未完全统一，见第 5 节。

### 2.5 当前命令路由

当前顶层入口 [`magi/__main__.py`](../magi/__main__.py) 识别 `runtime`、`webui` 和 `cli` 三种 service role。实际启动 CLI 的可用子命令由 [`magi/startup/cli.py`](../magi/startup/cli.py) 注册：

```bash
magi                         # 默认运行一个 MAGI Runtime
magi runtime                 # 运行一个 MAGI Runtime
magi webui                   # 运行 singleton WebUI
magi cli run                 # bootstrap + 运行一个 MAGI
magi cli create              # 在已有 MAGIS 下登记一个 MAGI
magi cli start               # 启动 detached MAGI 子进程
magi cli stop                # 停止一个 MAGI 子进程
magi cli restart             # 停止并重新启动
magi cli status              # 查询本地 slot 状态
magi cli webui               # 通过 CLI handler 运行 WebUI
```

`magi run`、`magi start`、`magi doctor`、`magi cli install-service` 和 `magi cli uninstall-service` 目前不能当作已经实现的稳定命令写入用户文档，除非顶层路由和对应 handler 实际补齐。

---

## 3. 应当保留的设计原则

### 3.1 一个 Runtime 只运行一个 MAGI

每个 MAGI 必须拥有：

- 独立 OS 进程或 Kubernetes Pod；
- 独立 Workspace；
- 独立私有 SQLite；
- 稳定的 MAGI 身份；
- 独立日志和运行状态；
- 自己的 provider、Tool 和 MCP 配置。

不在一个 Python 进程内运行多个 MAGI，也不通过不同目录来宣称获得安全隔离。

### 3.2 首个 MAGI bootstrap 必须幂等

当 `MAGIS_DATABASE_URL` 缺失时，首个 MAGI 执行：

1. 创建 MAGIS 数据库；
2. 创建 Genesis MAGIS；
3. 创建首个 `eva-000` 身份；
4. 创建 Membership 和必要的角色关系；
5. 创建私有 Workspace 和 SQLite；
6. 启动唯一 WebUI。

重复执行不得创建第二个 Genesis、第二个 `eva-000`、重复 Membership 或第二个 WebUI。

### 3.3 加入已有 MAGIS 不得自动注册未知身份

当提供 `MAGIS_DATABASE_URL` 时：

1. 必须提供 `MAGI_ID`；
2. 必须从 MAGIS 中读取该身份；
3. 身份不存在时启动失败；
4. 必须校验 `MAGI_NAME` 与持久化身份一致；
5. 必须校验 Workspace 中保存的身份与当前 MAGIS/`MAGI_ID` 一致；
6. 不得创建新的 Genesis、ADAM 或未知 MAGI；
7. 不得再次启动 WebUI。

Workspace 身份冲突必须失败，不能用新参数覆盖已有身份。

### 3.4 WebUI 是唯一浏览器入口

浏览器只访问 WebUI。WebUI 再通过 BUS registry 查询 Runtime endpoint，并附带：

- 目标 `magic_id`；
- 操作员身份；
- method/path/query；
- 时间戳；
- 短期 HMAC 签名。

Runtime 必须拒绝目标 ID 不匹配、签名过期、签名不正确或 scope 不合法的请求。Runtime proxy 的当前实现位于 [`magi/channels/api/runtime_proxy.py`](../magi/channels/api/runtime_proxy.py) 和 [`magi/channels/api/proxy_auth.py`](../magi/channels/api/proxy_auth.py)。

### 3.5 BUS 是模块协作和持久化边界

Local Profile 不能成为绕过 BUS 的例外：

```text
channels.api → BUS
Agent / Tools / MCP / Proactive → BUS
BUS → DB
```

业务模块不应直接构造 SQLAlchemy Engine、Session、ORM 或 Repository，也不应直接调用另一个业务模块的 Worker。具体边界以 [`docs/MAGI_BUS_CENTRIC_ARCHITECTURE.md`](MAGI_BUS_CENTRIC_ARCHITECTURE.md) 和架构测试为准。

启动层是 Composition Root，可以装配 BUS、DB、Worker 和外层资源；它不应把业务逻辑塞进 CLI handler 或进程管理器。

### 3.6 本地模式必须明确安全边界

Local Profile 默认是可信单用户模式：

- WebUI、Runtime 和控制面默认只应监听 loopback；
- 不允许把本地目录描述成 sandbox；
- Provider/API secret 不进入 argv、日志或 launch config；
- Workspace 路径必须 canonicalize 并检查边界；
- subprocess 使用 argv array，不使用 `shell=True`；
- 停止进程前验证进程身份；
- 删除 Runtime 默认保留或归档 Workspace。

Kubernetes 需要对外监听时，应通过 Kubernetes Service/Ingress 的 profile 配置实现，不能用一个全局 Host 常量同时满足两个 profile。

### 3.7 数据域必须分离

```text
MAGI 私有 SQLite
  sessions / memories / contacts / tasks / settings / tool catalog

MAGIS 公共数据库
  MAGIS / MAGIC / membership / roles / provider config / runtime registry
```

MAGIS 组织表不能写入某个 MAGI 的私有数据库。Local MAGIS SQLite 可以使用 WAL、foreign keys、busy timeout 和短事务，但正式升级必须使用版本化 migration。

### 3.8 Endpoint 必须来自 registry

WebUI、A2A 和其他调用方只能使用平台无关的 `RuntimeEndpoint`。不得从 `deployment_name`、固定端口或用户输入拼出 Runtime URL：

```python
endpoint = bus.registry.resolve_endpoint(magic_id)
```

Kubernetes-specific 字段可以保留在兼容 detail DTO 中，但不能重新成为普通调用方的必填字段。

---

## 4. 不再保留的旧设计

以下内容从原计划中移除，不应在新代码或新文档中继续作为目标：

| 旧设计 | 处理 |
| --- | --- |
| `RuntimeBackend` + `CLIProcessRuntimeBackend` 作为 Local/K8s 共同抽象 | 删除目标；本地进程和 K8s 资源分别归属 `magi.startup.local`、`magi.startup.kubernetes` |
| Local Orchestrator HTTP 服务和常驻多 Runtime supervisor | 删除目标；当前选择每个 MAGI 独立进程，后续只增加必要的 registry/reconcile |
| `~/.local/share/magi` 作为当前默认路径 | 不作为当前事实；实际路径必须由代码和部署脚本统一后再写入用户文档 |
| 独立的 `control/local-registry.db` | 删除目标；控制面状态应与对应 MAGIS 数据域保持一致 |
| 新建一个供所有业务模块导入的 `magi.runtime` 或 `LocalPathLayout` 公共层 | 删除目标；路径和存储配置由 Composition Root 注入，BUS 只暴露 DTO/服务 |
| 在本文重复完整 BUS、Tasks、MCP、Plugins 架构 | 删除；统一引用 BUS-centric 权威文档 |
| 预先规划几十个 Phase、文件清单和 Codex 输出格式 | 删除；当前只保留可验证的加固 backlog |
| 把 `install-service`、桌面壳、PyInstaller/Nuitka 产物描述成已经可用 | 删除当前实现表述；如需要，放入发布 roadmap |
| 用 `execve`、supervisor 或 systemd 的某一种实现描述当前事实 | 删除歧义；当前代码事实是 detached `subprocess.Popen`，未来实现必须单独记录 |

---

## 5. 当前实现与部署脚本的不一致

本节记录需要修复的实现问题。它们不是本文定义的最终行为。

### 5.1 CLI 路由和子进程 argv

[`magi/startup/local.py`](../magi/startup/local.py) 构造的子进程 argv 使用 `magi run`，但当前顶层 [`magi/__main__.py`](../magi/__main__.py) 并没有把 `run` 作为顶层 service role 路由；稳定做法应当是：

- 要么把 `magi run` 正式加入顶层兼容路由；
- 要么统一子进程 argv 为当前可路由的 `magi cli run`；
- 同时为 `magi run`、`magi start` 等旧/新别名补充 parser regression tests。

不能让注释、README、子进程 argv 和顶层 argparse 各自声明不同的命令面。

### 5.2 首次启动不能强行注入 `MAGIS_DATABASE_URL`

首个 MAGI 的判断规则是“缺失 `MAGIS_DATABASE_URL`”。但 [`deploy/cli/magi`](../deploy/cli/magi) 当前无条件设置本地 MAGIS DSN，这会把首个启动误判成“加入已有 MAGIS”。

修复原则：

- 首次启动保持 `MAGIS_DATABASE_URL` 未设置；
- 由 bootstrap 创建并返回 canonical MAGIS DSN；
- 只有加入已有 MAGIS 时才传入 `MAGIS_DATABASE_URL + MAGI_ID`；
- 包装脚本不得覆盖 Python 启动配置的 bootstrap 语义。

### 5.3 Runtime 端口、健康检查和多 Runtime

当前 Runtime 使用固定 `RUNTIME_PORT=42070`，而本地启动健康检查固定检查 `42069`。应明确区分：

```text
Kubernetes Profile
  每个 Pod 使用相同的内部 Runtime 端口，因为 Pod 有独立网络空间

CLI Profile
  WebUI 固定 42069
  每个 MAGI 从持久化端口范围分配，例如 42101-42999
```

CLI 端口分配必须：

1. 在 registry 中原子 claim；
2. 记录 `runtime_id`、port、endpoint 和 revision；
3. 重启优先复用原端口；
4. 被外部程序占用时重新分配；
5. health check 使用该 Runtime 的实际 endpoint；
6. 释放、停止和删除时更新 registry。

不能用固定端口加 PID 文件实现多个本地 MAGI。

### 5.4 Local WebUI Host 和 control secret

Kubernetes WebUI 可以绑定 `0.0.0.0`，但本地 WebUI 默认必须绑定 `127.0.0.1`。需要把 Host 选择移到 profile-specific startup 配置。

`ensure_control_secret()` 已存在，但必须接入真实启动流程：

1. 首次启动生成随机 secret；
2. 以当前用户可读权限写入数据根目录；
3. 通过受控环境或安全文件句柄传给 WebUI 和 Runtime；
4. 不写入 argv、日志或公开配置；
5. WebUI 和 Runtime 使用同一 secret 完成 HMAC proxy 校验。

### 5.5 Workspace、私有 DB 和 MAGIS DB 的 canonical layout

当前代码中存在多套路径约定：

- `StartupConfig.workspace_dir` 推导 `<HOST_WORKSPACE_DIR>/MAGI_Citizens/<MAGI_NAME>`；
- `paths.py` 的私有数据库 helper 与 Runtime 的 `memories/magi.db` state_dir 约定不完全一致；
- `deploy/cli/install.sh` 仍创建 `MAGIC/MAGIS`；
- `deploy/cli/magi`、部署 README 和 Python path helper 使用了不同的 MAGIS 目录名和 slug。

在代码统一前，本文不再伪造一份“已完成”的目录树。最终应满足以下不变量：

```text
HOST_WORKSPACE_DIR/
├── MAGI_Citizens/<stable-magi-slug>/
│   ├── memories/magi.db        # 私有 Runtime 状态
│   ├── skills/
│   ├── logs/
│   └── run/
└── MAGI_Societies/<stable-magis-slug>/
    └── magis.db                 # MAGIS 公共与控制面状态
```

稳定 ID 或稳定 slug 必须参与实际目录名；显示名变化不能导致数据丢失或切换到另一个身份。所有脚本、测试、Python helper 和文档必须使用同一布局。

### 5.6 Runtime registry、reconcile 和 PID 身份

当前已经存在 control registry 的模型和 Repository，但本地 `start/stop/status` 仍主要依赖 PID 文件。目标流程应为：

```text
start
  → claim registry row + port
  → prepare workspace and launch config
  → spawn process
  → validate process identity
  → health check endpoint
  → record observed state

restart / launcher crash
  → load registry
  → verify PID + process token + endpoint
  → mark stale or recover
  → never kill an unrelated process
```

Registry 至少应保存：

```text
runtime_id
backend_kind
workspace_dir
launch_config_path
pid
process_start_token
executable_identity
port
endpoint_url
desired_state
observed_state
last_health_at
last_error
```

### 5.7 数据库 migration

当前 MAGIS 初始化仍使用 `metadata.create_all()` 创建部分 control tables。正式 CLI 发布前应：

- 为 MAGIS 公共表和 control registry 提供版本化 migration；
- 启动时执行 `upgrade head`；
- migration 失败时停止启动，不继续运行半升级数据库；
- 升级前提供备份或明确的恢复策略；
- 增加旧数据库升级、降级失败和并发启动测试；
- 不在一次 migration 中移动 Workspace 或删除用户数据。

### 5.8 停止、删除和归档

当前 stop 只负责停止进程。后续 lifecycle 应区分：

```text
stop
  → desired_state=stopped
  → 优雅停止
  → observed_state=stopped
  → 保留 Workspace

archive/delete
  → 停止 Runtime
  → 从 active registry 移除
  → Workspace 移入可恢复 archive

purge
  → 单独的显式、带确认的永久删除
```

停止进程或删除 registry row 不能递归删除 Workspace。

### 5.9 Endpoint URL cleanup

Runtime proxy 已经优先查询 `bus.registry.resolve_endpoint()`，但部分 BUS service 仍保留从 `deployment_name` 拼接 `http://...:42069` 的兼容路径。应按以下顺序清理：

1. 新 Runtime row 始终写入真实 endpoint；
2. 调用方统一走 registry query；
3. 旧 K8s 字段只做兼容双读；
4. 完成数据回填后删除固定 DNS/端口 fallback；
5. Local 和 K8s 都通过同一平台无关 DTO 暴露 endpoint。

---

## 6. 发布范围与可选 roadmap

以下内容不属于当前 CLI MVP，只有在产品需要“用户无需 Python/uv 的安装包”时才启用：

- macOS、Windows、Linux standalone artifacts；
- SPA 静态资源随 wheel 或 executable 发布；
- clean-machine smoke tests；
- 签名、notarization、MSI/AppImage 等安装格式；
- systemd user unit 自动生成；
- 托盘、桌面壳和自动更新。

发布原则必须保留：

- 应用代码和用户数据分离；
- 升级不覆盖 Workspace；
- 卸载默认不删除用户数据；
- 所有平台的 migration、secret、MCP subprocess 和 Workspace 边界行为一致；
- 文档明确 Local Profile 不是安全 sandbox。

在这些事项真正实现之前，用户文档只应写当前可执行的 Python/CLI 安装前置条件，不应宣称“不需要 Python、uv 或 Node”。

---

## 7. 最小验证清单

### CLI 和 bootstrap

- [ ] 顶层命令、`magi cli` 命令和子进程 argv 三者一致；
- [ ] 全新目录首次启动成功；
- [ ] 首次启动不因包装脚本提前设置 MAGIS DSN 而走错分支；
- [ ] 重启不重复创建 Genesis、`eva-000`、Membership 或 WebUI；
- [ ] 加入已有 MAGIS 时缺少或伪造 `MAGI_ID` 会失败；
- [ ] Workspace identity conflict 会阻止启动。

### 进程和 Runtime

- [ ] 每个本地 MAGI 是独立进程；
- [ ] 两个 MAGI 可以同时运行且端口不冲突；
- [ ] health check 使用真实 endpoint，而不是固定错误端口；
- [ ] stale PID 不会误杀被复用的 PID；
- [ ] launcher/终端退出不会留下无法识别的孤儿进程；
- [ ] stop 有优雅停止、超时和安全强杀路径；
- [ ] registry 与实际进程状态可以 reconcile。

### 安全

- [ ] Local WebUI 默认只监听 `127.0.0.1`；
- [ ] control secret 首次生成、权限、传递和轮换可验证；
- [ ] secret、provider key 和带凭据 DSN 不出现在 argv 或日志；
- [ ] `MAGI_NAME`、Workspace 和 archive 路径通过 canonicalization 和边界检查；
- [ ] subprocess 不使用 `shell=True`；
- [ ] Runtime proxy 校验目标 ID、method/path、时间戳、scope 和 HMAC。

### 数据和升级

- [ ] Local MAGIS 使用独立 SQLite，不写入 MAGI 私有 DB；
- [ ] WAL、foreign keys、busy timeout 和短事务策略有效；
- [ ] 全新数据库执行 migration；
- [ ] 旧数据库可升级；
- [ ] migration 失败不会启动 Runtime；
- [ ] stop/delete/archive/purge 的数据语义分别测试；
- [ ] Workspace 升级和卸载后仍然保留。

### Kubernetes 回归

- [ ] K8s Pod 仍然一个 Pod 一个 MAGI；
- [ ] K8s Runtime 可以使用固定内部端口；
- [ ] K8s WebUI 和 Runtime 的网络边界不被 Local Profile 放宽；
- [ ] MAGIS PostgreSQL、PVC、Secret、RBAC 和 ServiceAccount 行为不回归；
- [ ] K8s 和 CLI 共享相同的 bootstrap、Runtime、WebUI 和 BUS 业务边界。

---

## 8. 文档维护规则

本文是非权威设计记录，不再维护“预计修改文件”“Phase 0-9 执行步骤”或“Codex 每阶段汇报模板”。以后如果实现发生变化，只在以下位置更新正式行为：

1. 代码和自动化测试；
2. [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) 的架构不变量；
3. [`deploy/cli/README.md`](../deploy/cli/README.md) 的用户可执行命令和数据位置；
4. 发布或 roadmap 文档中的未来能力。

当第 5 节的 P0/P1 加固项完成、引用本文件的历史审计文档被清理、部署脚本与代码契约统一后，可以删除本文。
