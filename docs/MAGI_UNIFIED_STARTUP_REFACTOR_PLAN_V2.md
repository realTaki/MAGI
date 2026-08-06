# MAGI Unified Startup Refactor Plan

> 面向 Codex 的实现计划  
> 目标：统一 MAGI 的启动、Bootstrap、本地进程创建、Kubernetes 资源创建以及唯一 WebUI 的启动逻辑。

---

## 1. 核心目标

本次重构只解决一个领域：

> 如何启动一个 MAGI，以及如何为它准备运行环境。

所有与启动相关的代码统一收敛到：

```text
magi.startup
```

不再将 Runtime、CLI 和 Kubernetes 编排拆成并列的顶层启动体系。

本地进程与 Kubernetes 的差异只存在于外层资源准备：

```text
本地：
创建目录 + 启动进程

Kubernetes：
创建 PVC + Deployment + Service
```

最终每个 MAGI 都进入同一个 Bootstrap 和 Runtime 启动流程。

---

## 2. 基本约束

### 2.1 一个 Runtime 只运行一个 MAGI

每个 MAGI 都拥有：

- 独立 Workspace；
- 独立私有数据库；
- 独立进程或容器；
- 唯一 MAGI 身份；
- 可选的所属 MAGIS。

容器中不会启动第二个 MAGI。

本地启动第二个 MAGI 时，也必须启动第二个独立进程。

---

### 2.2 第一个 MAGI 固定为 `eva-000`

本地首次执行：

```bash
magi run
```

系统默认解析：

```text
HOST_WORKSPACE_DIR = ~/.magi
MAGI_NAME = eva-000
```

Workspace 自动推导为：

```text
~/.magi/MAGI_Citizens/eva-000
```

用户不需要传入完整路径。

---

### 2.3 整个 MAGIS 只有一个 WebUI

启动第一个 MAGI `eva-000` 时，同时启动唯一 WebUI：

```text
Bootstrap MAGIS
    ↓
Bootstrap eva-000
    ↓
Start eva-000 Runtime
    ↓
Start singleton WebUI
```

后续 MAGI：

```text
eva-001
eva-002
...
```

只启动各自 Runtime，不再启动 WebUI。

WebUI 是整个 MAGIS 的统一入口，不是普通 MAGI 的私有组件。

---

## 3. 如何判断是否需要 Bootstrap MAGIS

唯一核心判断：

```text
是否提供 MAGIS_DATABASE_URL？
```

### 3.1 未提供 MAGIS

说明当前环境尚未初始化 MAGIS。

系统需要：

1. 创建 MAGIS 数据库；
2. 创建 Genesis MAGIS；
3. 创建 `eva-000` 身份；
4. 创建 Membership；
5. 将 `eva-000` 设置为第一个 ADAM；
6. 初始化 `eva-000` 私有 Workspace；
7. 启动 `eva-000` Runtime；
8. 启动唯一 WebUI。

---

### 3.2 已提供 MAGIS

说明当前 MAGI 已属于现有 MAGIS。

系统需要：

1. 连接 `MAGIS_DATABASE_URL`；
2. 根据 `MAGI_ID` 查找自身；
3. 验证 Membership 和 Role；
4. 根据 Host Workspace 和 MAGI 名称推导 Workspace；
5. 初始化私有数据库和本地目录；
6. 启动当前 MAGI Runtime。

它不应该：

- 创建新的 MAGIS；
- 创建新的 Genesis；
- 创建第二个 ADAM；
- 自动向现有 MAGIS 注册未知身份；
- 启动第二个 WebUI。

---

## 4. 启动契约

Runtime 启动契约只包含：

```text
HOST_WORKSPACE_DIR
MAGI_NAME
MAGIS_DATABASE_URL
MAGI_ID
```

### 4.1 `HOST_WORKSPACE_DIR`

本地默认值：

```text
~/.magi
```

容器中可以设置为 PVC 的挂载根目录，例如：

```text
/workspace
```

---

### 4.2 `MAGI_NAME`

第一个 MAGI 默认：

```text
eva-000
```

后续 MAGI 示例：

```text
eva-001
eva-002
```

名称参与 Workspace 路径推导。

---

### 4.3 `MAGIS_DATABASE_URL`

可选。

SQLite：

```text
sqlite:////absolute/path/to/magis.db
```

PostgreSQL：

```text
postgresql+psycopg://user:password@host:5432/magis
```

未提供时表示需要 Bootstrap 第一个 MAGIS。

---

### 4.4 `MAGI_ID`

加入已有 MAGIS 时必填。

它必须是 MAGIS 中的持久身份，而不是：

- PID；
- Pod 名称；
- 临时 Runtime ID；
- 可修改显示名称。

---

## 5. 不属于启动契约的配置

最终 Workspace 路径不作为输入参数或环境变量。

Workspace 永远由以下两项推导：

```text
HOST_WORKSPACE_DIR + MAGI_NAME
```

调用方不能直接传入最终 Workspace。

Runtime 的内部监听地址和内部端口硬编码。不同容器拥有独立网络命名空间，相同内部端口不会冲突。Runtime 不直接对外暴露，只有 WebUI 统一对外暴露。

开发环境是否启用 reload，由开发镜像或开发启动角色硬编码。

部署 Backend、节点角色、Kubernetes namespace、PVC 名称和 Pod 名称都属于部署层，不参与 MAGI Bootstrap。

---

## 6. Workspace 推导

统一函数：

```python
from pathlib import Path


def resolve_workspace(
    host_workspace_dir: Path,
    magi_name: str,
) -> Path:
    return (
        host_workspace_dir
        / "MAGI_Citizens"
        / magi_name
    )
```

本地：

```text
HOST_WORKSPACE_DIR = ~/.magi
MAGI_NAME = eva-000

=> ~/.magi/MAGI_Citizens/eva-000
```

Kubernetes：

```text
HOST_WORKSPACE_DIR = /workspace
MAGI_NAME = eva-001

=> /workspace/MAGI_Citizens/eva-001
```

本地、Docker 和 Kubernetes 使用完全相同的推导规则。

---

## 7. 推荐目录结构

所有启动相关实现放入：

```text
magi/startup/
```

目标结构：

```text
magi/
├── __main__.py
├── startup/
│   ├── __init__.py
│   ├── config.py
│   ├── paths.py
│   ├── context.py
│   ├── bootstrap.py
│   ├── runtime.py
│   ├── local.py
│   ├── webui.py
│   ├── kubernetes.py
│   └── cli.py
├── bus/
├── agent/
├── channels/
├── tools/
├── connectors/
├── plugins/
├── models/
└── <existing webui product module>
```

---

## 8. `magi.startup.config`

定义启动配置：

```python
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StartupConfig:
    host_workspace_dir: Path
    magi_name: str
    magis_database_url: str | None
    magi_id: str | None

    @property
    def workspace_dir(self) -> Path:
        return (
            self.host_workspace_dir
            / "MAGI_Citizens"
            / self.magi_name
        )
```

职责：

- 读取 CLI 参数；
- 读取环境变量；
- 应用默认 Host Workspace；
- 应用默认 `eva-000`；
- 标准化数据库 URL；
- 校验参数组合；
- 推导最终 Workspace。

校验：

```python
if (
    config.magis_database_url is not None
    and config.magi_id is None
):
    raise ConfigurationError(
        "MAGI_ID is required when joining an existing MAGIS"
    )
```

该模块不执行文件系统或数据库副作用。

---

## 9. `magi.startup.paths`

负责所有启动路径推导：

```python
resolve_host_workspace()
resolve_magi_workspace()
resolve_magis_database_path()
resolve_private_database_path()
resolve_runtime_state_path()
resolve_runtime_pid_path()
resolve_runtime_log_paths()
resolve_webui_pid_path()
resolve_webui_log_paths()
```

本地建议结构：

```text
~/.magi/
├── MAGI_Citizens/
│   ├── eva-000/
│   │   ├── magi.db
│   │   ├── runtime.json
│   │   ├── skills/
│   │   ├── memories/
│   │   ├── logs/
│   │   └── run/
│   └── eva-001/
│       └── ...
├── MAGI_Societies/
│   └── genesis/
│       └── magis.db
├── run/
│   └── webui.pid
└── logs/
    ├── webui.stdout.log
    └── webui.stderr.log
```

WebUI PID 和日志放在 Host Workspace 根目录，因为 WebUI 属于整个 MAGIS。

---

## 10. `magi.startup.context`

Bootstrap 完成后生成：

```python
@dataclass(frozen=True)
class StartupContext:
    host_workspace_dir: Path
    workspace_dir: Path
    magi_name: str
    magi_id: str
    magis_database_url: str
    private_database_url: str
    is_first_magi: bool
```

BUS、Agent、Channels 和 Tools 使用显式 Context。

不要在运行期间反复读取或修改环境变量。

---

## 11. `magi.startup.bootstrap`

统一入口：

```python
def bootstrap_magi(
    config: StartupConfig,
) -> StartupContext:
    workspace_dir = ensure_workspace(
        config.workspace_dir
    )

    if config.magis_database_url is None:
        identity = bootstrap_first_magi(
            config=config,
            workspace_dir=workspace_dir,
        )
    else:
        identity = bootstrap_existing_magi(
            config=config,
            workspace_dir=workspace_dir,
        )

    private_database_url = ensure_private_database(
        workspace_dir=workspace_dir,
        identity=identity,
    )

    persist_runtime_state(
        workspace_dir=workspace_dir,
        identity=identity,
        private_database_url=private_database_url,
    )

    return StartupContext(
        host_workspace_dir=config.host_workspace_dir,
        workspace_dir=workspace_dir,
        magi_name=config.magi_name,
        magi_id=identity.magi_id,
        magis_database_url=identity.magis_database_url,
        private_database_url=private_database_url,
        is_first_magi=identity.is_first_magi,
    )
```

---

## 12. Bootstrap 第一个 MAGI

```python
def bootstrap_first_magi(
    config: StartupConfig,
    workspace_dir: Path,
) -> BootstrapIdentity:
    if config.magi_name != "eva-000":
        raise ConfigurationError(
            "The first MAGI must be eva-000"
        )

    magis_database_url = determine_default_magis_url(
        config.host_workspace_dir
    )

    magis = ensure_magis_database(
        magis_database_url
    )

    genesis = ensure_genesis_magis(magis)
    magi = ensure_first_magi_identity(
        magis=magis,
        genesis=genesis,
        name="eva-000",
    )

    ensure_adam_membership(
        magis=magis,
        genesis=genesis,
        magi=magi,
    )

    return BootstrapIdentity(
        magi_id=magi.id,
        magis_database_url=magis_database_url,
        is_first_magi=True,
    )
```

所有操作必须幂等。

重复启动时：

- 不创建第二个 Genesis；
- 不创建第二个 `eva-000`；
- 不创建重复 Membership；
- 不覆盖原有私有数据。

---

## 13. Bootstrap 已有 MAGIS 中的 MAGI

```python
def bootstrap_existing_magi(
    config: StartupConfig,
    workspace_dir: Path,
) -> BootstrapIdentity:
    magis = connect_magis(
        config.magis_database_url
    )

    magi = require_existing_magi(
        magis=magis,
        magi_id=config.magi_id,
    )

    require_valid_membership(
        magis=magis,
        magi_id=config.magi_id,
    )

    validate_magi_name(
        expected=config.magi_name,
        actual=magi.name,
    )

    validate_workspace_identity(
        workspace_dir=workspace_dir,
        magis_database_url=config.magis_database_url,
        magi_id=config.magi_id,
    )

    return BootstrapIdentity(
        magi_id=magi.id,
        magis_database_url=config.magis_database_url,
        is_first_magi=False,
    )
```

如果 `MAGI_ID` 不存在，启动失败。

Runtime 不允许自动注册自己。

---

## 14. `magi.startup.runtime`

负责运行一个 MAGI：

```python
async def run_magi(
    config: StartupConfig,
) -> None:
    context = bootstrap_magi(config)

    bus = build_bus(context)
    workers = build_workers(context, bus)
    channels = build_channels(context, bus)
    api = build_runtime_api(context, bus)

    async with runtime_lifespan(
        context=context,
        bus=bus,
        workers=workers,
        channels=channels,
    ):
        await serve_runtime_api(api)
```

该模块不负责：

- 创建其他 MAGI；
- 管理本地子进程；
- 创建 Kubernetes 资源；
- 创建 PVC；
- 启动或管理 WebUI；
- 动态配置 Host、Port 或 Reload；
- 管理多个 Runtime。

---

## 15. `magi.startup.webui`

只负责唯一 WebUI 的生命周期，不包含 WebUI 产品实现。

本地职责：

```text
start_webui()
stop_webui()
get_webui_status()
ensure_webui_running()
```

本地 PID：

```text
~/.magi/run/webui.pid
```

本地日志：

```text
~/.magi/logs/webui.stdout.log
~/.magi/logs/webui.stderr.log
```

Kubernetes 职责：

```text
ensure_webui_deployment()
ensure_webui_service()
get_webui_status()
delete_webui_resources()
```

约束：

- 仅首次启动或恢复 `eva-000` 时调用；
- 后续 MAGI 不得创建第二个 WebUI；
- WebUI 产品代码继续保留在现有模块；
- `startup.webui` 只调用现有 WebUI 入口。

---

## 16. `magi.startup.local`

负责本地外层资源和进程。

### 创建第二个 MAGI

```bash
magi create \
  --name eva-001 \
  --magis sqlite:////path/to/magis.db \
  --start
```

流程：

1. 连接现有 MAGIS；
2. 验证创建权限；
3. 创建 MAGI 身份；
4. 创建 Membership；
5. 根据 Host Workspace 和名称推导 Workspace；
6. 创建必要目录；
7. 启动独立进程；
8. 保存 PID 和日志。

最终子进程：

```bash
magi run \
  --name eva-001 \
  --magis sqlite:////path/to/magis.db \
  --magi-id <id>
```

### 本地进程管理

```bash
magi start --name eva-001
magi stop --name eva-001
magi restart --name eva-001
magi status --name eva-001
```

每个 MAGI：

```text
<workspace>/run/magi.pid
<workspace>/logs/stdout.log
<workspace>/logs/stderr.log
```

`eva-000` 启动成功后：

```python
ensure_webui_running()
```

其他 MAGI 不调用该函数。

---

## 17. `magi.startup.kubernetes`

当前只有 Kubernetes，不建立额外的 Backend 抽象。

职责：

- 在 MAGIS 中创建 MAGI 身份；
- 创建 Membership；
- 创建 PVC；
- 创建 MAGI Deployment；
- 创建必要的内部 Service；
- 查询和删除资源；
- 首次部署 `eva-000` 时创建唯一 WebUI Deployment 和对外 Service。

### 普通 MAGI Deployment

```yaml
command: ["magi"]
args: ["run"]

env:
  - name: HOST_WORKSPACE_DIR
    value: /workspace

  - name: MAGI_NAME
    value: eva-001

  - name: MAGIS_DATABASE_URL
    valueFrom:
      secretKeyRef:
        name: magis-database
        key: url

  - name: MAGI_ID
    value: "<id>"
```

PVC 挂载到：

```text
/workspace
```

实际 MAGI Workspace 自动推导为：

```text
/workspace/MAGI_Citizens/eva-001
```

### 第一个 MAGI

首次部署 `eva-000` 时创建：

```text
eva-000 PVC
eva-000 Deployment
WebUI Deployment
WebUI external Service
```

后续 MAGI 不创建 WebUI。

---

## 18. `magi.startup.cli`

定义命令：

```text
magi run
magi create
magi start
magi stop
magi restart
magi status
```

调用关系：

```text
magi run
    -> magi.startup.cli.run
    -> magi.startup.runtime.run_magi
```

```text
magi create
    -> magi.startup.cli.create
    -> magi.startup.local
       or magi.startup.kubernetes
```

兼容旧入口时，也只能转发到 `magi.startup`。

---

## 19. `magi/__main__.py`

只保留命令路由：

```python
def main() -> None:
    app = build_startup_cli()
    app()
```

不要在 `__main__.py` 中继续实现：

- Bootstrap；
- BUS 组合；
- WebUI 进程管理；
- Kubernetes 资源创建；
- PID 管理。

---

## 20. 对现有模块的处理

### 20.1 `magi.launcher`

职责迁移：

| 当前职责 | 新位置 |
|---|---|
| 启动配置解析 | `magi.startup.config` |
| 路径推导 | `magi.startup.paths` |
| MAGIS/MAGI Bootstrap | `magi.startup.bootstrap` |
| Runtime 组合 | `magi.startup.runtime` |
| 本地进程管理 | `magi.startup.local` |
| 唯一 WebUI 生命周期 | `magi.startup.webui` |
| Kubernetes 资源创建 | `magi.startup.kubernetes` |
| 命令实现 | `magi.startup.cli` |

迁移完成后删除 `magi.launcher`。

---

### 20.2 现有 Runtime 启动代码

现有分散在 `__main__.py`、launcher 或其他模块中的 Runtime 启动组合，全部迁移到：

```text
magi.startup.runtime
```

不创建独立的顶层 Runtime 启动包。

---

### 20.3 现有 CLI 启动代码

现有 start、stop、status、PID、日志和 spawn 逻辑迁移到：

```text
magi.startup.local
magi.startup.cli
```

不创建独立的顶层 CLI 启动包。

---

### 20.4 现有 Orchestrator 启动代码

与创建 MAGI 相关的 Kubernetes 资源操作迁移到：

```text
magi.startup.kubernetes
```

不为 Kubernetes 创建额外的 Backend 分层。

如果仓库中已有通用 Kubernetes 客户端封装，可继续保留为基础设施代码，但“如何创建并启动一个 MAGI”的流程必须位于 `magi.startup.kubernetes`。

---

### 20.5 WebUI

WebUI 产品实现保持现有位置。

只将以下逻辑放入：

```text
magi.startup.webui
```

- 唯一实例判断；
- 本地进程启动；
- PID 和日志；
- K8s Deployment；
- K8s Service；
- 状态与恢复。

---

### 20.6 `CLIProcessRuntimeBackend`

删除该抽象。

必要能力迁入：

```text
magi.startup.local
```

不再把本地进程管理伪装成 Kubernetes Backend。

---

## 21. WebUI 与网络边界

### Runtime

- 固定内部 Host；
- 固定内部端口；
- 不直接对外暴露；
- 不接受 Host 或 Port 配置。

### WebUI

- 唯一对外暴露模块；
- 整个 MAGIS 只有一个；
- 与 `eva-000` 一起创建和恢复；
- 后续 MAGI 不创建 WebUI。

### Reload

- Production 启动角色硬编码关闭；
- Development 启动角色硬编码开启；
- 不使用 `MAGI_RELOAD`。

---

## 22. 数据一致性

### 创建新 MAGI

以下操作尽量在同一事务中完成：

- MAGI identity；
- Membership；
- Role；
- 初始 provision 状态。

建议状态：

```text
created
provisioning
bootstrapping
running
stopped
provision_failed
bootstrap_failed
```

MAGI 记录存在不等于 Runtime 已运行。

---

### Workspace 身份冲突

如果 Workspace 中已保存：

```json
{
  "magi_id": "A",
  "magis_database_url": "X"
}
```

但当前启动参数解析为：

```text
MAGI_ID = B
MAGIS_DATABASE_URL = Y
```

必须失败，不得覆盖。

---

## 23. 实施阶段

### Phase 1：建立 `magi.startup`

1. 创建 `magi/startup/`；
2. 创建 config、paths、context；
3. 定义 `StartupConfig`；
4. 实现 Workspace 推导；
5. 删除显式 Workspace 参数解析；
6. 添加配置单元测试。

### Phase 2：统一 Bootstrap

1. 创建 `startup/bootstrap.py`；
2. 迁移第一个 MAGIS Bootstrap；
3. 实现 `eva-000` 初始化；
4. 实现已有 MAGIS 身份加载；
5. 实现 runtime.json；
6. 添加幂等测试。

### Phase 3：统一 Runtime

1. 创建 `startup/runtime.py`；
2. 迁移 BUS 组合；
3. 迁移 Worker 生命周期；
4. 迁移 Channel 生命周期；
5. 迁移 Runtime API；
6. 让 `magi run` 使用新入口。

### Phase 4：本地进程

1. 创建 `startup/local.py`；
2. 实现 create/start/stop/restart/status；
3. 实现 PID 和日志；
4. 删除复杂 CLI Backend；
5. 首次启动 `eva-000` 时启动 WebUI。

### Phase 5：唯一 WebUI

1. 创建 `startup/webui.py`；
2. 实现本地单例检查；
3. 实现 WebUI PID 和日志；
4. 实现 K8s WebUI Deployment 和 Service；
5. 实现恢复逻辑；
6. 验证后续 MAGI 不启动 WebUI。

### Phase 6：Kubernetes

1. 创建 `startup/kubernetes.py`；
2. 创建 MAGI 身份和 Membership；
3. 创建 PVC；
4. 创建 Deployment；
5. 传递 Host Workspace、名称、MAGIS 和 MAGI ID；
6. 首次部署创建唯一 WebUI；
7. 删除 backend 抽象。

### Phase 7：清理

1. 迁移全部 `magi.launcher` import；
2. 删除 launcher；
3. 删除旧 CLI 启动路径；
4. 删除旧 Orchestrator Backend 路径；
5. 删除废弃环境变量；
6. 更新部署文件和文档。

---

## 24. 测试计划

### 配置

- 默认 Host Workspace 为 `~/.magi`；
- 默认 MAGI 名称为 `eva-000`；
- Workspace 始终由 Host Workspace 和名称推导；
- 不接受显式 Workspace；
- 已有 MAGIS 但缺少 MAGI ID 时失败。

### 第一个 MAGI

- 创建 MAGIS；
- 创建 Genesis；
- 创建 `eva-000`；
- 创建 ADAM Membership；
- 启动唯一 WebUI；
- 重启不重复创建任何记录或 WebUI。

### 后续 MAGI

- 根据名称推导独立 Workspace；
- 加载已有身份；
- 不创建 Genesis；
- 不创建第二个 WebUI；
- 错误 MAGI ID 启动失败。

### 本地进程

- create；
- start；
- stop；
- restart；
- status；
- stale PID；
- 日志；
- WebUI 单例。

### Kubernetes

- PVC 挂载为 Host Workspace；
- 传入 `HOST_WORKSPACE_DIR`；
- 传入 `MAGI_NAME`；
- 不传最终 Workspace 路径；
- 不传 Host、Port、Reload；
- 每个 Pod 一个 MAGI；
- 只有 WebUI Service 对外暴露；
- 后续 MAGI 不创建 WebUI。

---

## 25. 明确不做

本次不实现：

- Runtime、CLI 和 Kubernetes 三套并列的顶层启动包；
- Kubernetes Backend 抽象层；
- Profile 系统；
- 本地 Orchestrator HTTP 服务；
- 一个进程运行多个 MAGI；
- 一个容器运行多个 MAGI；
- Runtime 自动注册未知身份；
- 用户手动配置 Runtime Host、Port 或 Reload；
- 全量 WebUI 产品重构。

---

## 26. Definition of Done

完成后必须满足：

1. 所有启动相关代码位于 `magi.startup`；
2. `magi run` 默认启动 `eva-000`；
3. 默认 Host Workspace 为 `~/.magi`；
4. Workspace 由 Host Workspace 和 MAGI 名称推导；
5. 最终 Workspace 路径不是启动变量；
6. 未提供 MAGIS 时 Bootstrap 第一个 MAGIS；
7. 已提供 MAGIS 时按 MAGI ID 加载自身；
8. 每个 Runtime 只运行一个 MAGI；
9. 启动 `eva-000` 时启动唯一 WebUI；
10. 后续 MAGI 不启动 WebUI；
11. Runtime 不直接对外暴露；
12. 只有 WebUI 对外暴露；
13. 本地使用独立目录和进程；
14. Kubernetes 使用独立 PVC 和 Deployment；
15. 不存在无实际多态需求的 Kubernetes Backend 抽象；
16. 旧 launcher 和重复启动路径被删除。

---

## 27. 给 Codex 的执行要求

1. 先阅读当前 `magi/__main__.py`、`magi/launcher/`、现有 Runtime 组合、CLI 进程管理、Kubernetes 创建逻辑和 WebUI 启动逻辑。
2. 创建 `magi.startup` 后再逐步迁移。
3. 不创建 Runtime、CLI 或 Kubernetes 三套并列的顶层启动实现。
4. 不创建无实际多态需求的 Kubernetes Backend 抽象。
5. 不保留任何直接传入最终 Workspace 的变量或 CLI 参数。
6. Workspace 只能由 Host Workspace 和 MAGI 名称推导。
7. `eva-000` 必须是第一个 MAGI。
8. 启动 `eva-000` 必须同时启动唯一 WebUI。
9. 后续 MAGI 不得创建第二个 WebUI。
10. 不允许 Runtime 在已有 MAGIS 中自动创建未知身份。
11. Bootstrap 必须幂等。
12. 每个 Phase 独立提交并保持测试通过。
13. 删除 launcher 前搜索并迁移所有 import。
14. 最终更新 README、Dockerfile、K8s manifests 和开发部署文档。
