# MAGI 部署

`deploy/` 下提供两种部署方式，按工作场景选择：

| 场景 | 路径 | 入口 |
| --- | --- | --- |
| 单机本地（非容器 / CLI） | [deploy/cli/](cli/) | `./deploy/cli/install.sh`（安装、初始化并启动） |
| k8s 生产（已有集群） | [deploy/k8s/](k8s/) | `./deploy/k8s/bootstrap-k8s.sh` |

下面这张决策树帮你选路径：

```text
                    ┌─ 我只想跑一个本地 MAGI 试试 ─── deploy/cli/
                    │
你想做什么？ ────────┴─ 我要把 MAGI 部署到现有集群 ─────── deploy/k8s/
```

## 两种方式的差异

|  | CLI（非容器） | k8s（已有集群） |
| --- | --- | --- |
| 容器 | 否 | 是 |
| 运行时 | `magi node run`（服务管理器使用 `--foreground`） | Pod（`magi:0.1.0`） |
| 进程模型 | 每个 MAGI 独立 OS 进程 | 每个 MAGI 独立 Pod |
| 代码生效方式 | 重启进程 | 推送新镜像后滚动更新 Pod |
| 源码映射 | 否 | 否 |
| WebUI 端口 | 42069（Adam）/ 42070+（EVA） | 42069（需 port-forward） |
| WebUI 绑定 | `127.0.0.1`（默认 loopback） | `0.0.0.0`（K8s `MAGI_WEBUI_HOST`） |
| 持久化 | `~/.magi/MAGI_Citizens/<name>/memories/magi.db` | PVC `/MAGI_Citizens/<name>/memories/magi.db` |
| 注册成服务 | 是（每 MAGI 独立 systemd unit） | 否 |
| 唯一前置 | Python 3.12+ | 现有 k8s 集群 |

## 共享文件

```text
deploy/
├── Dockerfile              # Kubernetes 生产镜像
└── .tools/                 # 部署脚本下载的固定工具（kubectl 等）
```

`Dockerfile` 是唯一的 Kubernetes 镜像定义；CLI 路径**不**使用 Docker。

## 共享意图

两种方式提供同一个**应用抽象**：

- 每个 MAGI 的私有 SQLite（`MAGI_Citizens/<name>/memories/magi.db`）+ 工作区；
- 每个 MAGIS 的独立数据库（K8s: PostgreSQL，CLI: SQLite）+ 公共工作区；
- 一个 `magi-webui` 入口作为唯一浏览器界面。

路径解析由环境变量驱动：
- K8s Pod：不传 `HOST_WORKSPACE_DIR`。`magi.startup.paths` 通过
  `KUBERNETES_SERVICE_HOST` 自动检测 K8s 模式，默认 `HOST_WORKSPACE_DIR=/`；
  PVC 挂载到容器根 `/`，workspace 推导为 `/MAGI_Citizens/<name>`。
  如需覆盖可显式传入。
- CLI 进程：`HOST_WORKSPACE_DIR`（默认 `~/.magi`）+ `MAGI_NAME`。
  不存在硬编码的 `/workspace` 路径。`py-magi/magi/startup/paths.py` 是唯一暴露
  路径布局的地方。其余代码只读环境变量，不假设任何具体 mount 类型。

WebUI 绑定 host 由 `MAGI_WEBUI_HOST` 决定：
- K8s Pod：`deploy/k8s/control/webui-deployment.yaml` 显式传
  `MAGI_WEBUI_HOST=0.0.0.0`，让 ClusterIP / NodePort 能把外部流量转进
  pod。容器自己的 network namespace 隔离下，绑 `0.0.0.0` 只在 pod 内可见，
  不会泄漏到节点其它接口。
- CLI 进程：默认 `127.0.0.1`（loopback），仅同机 operator 的浏览器能
  访问。如需把 WebUI 暴露给同网段其它机器（例如开发期临时端口转发），
  可在启动前 `export MAGI_WEBUI_HOST=0.0.0.0` 后再 `magi start`。
