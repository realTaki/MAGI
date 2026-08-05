# MAGI 部署

`deploy/` 下提供**三种**并行部署方式，按工作场景选择：

| 场景 | 路径 | 入口 |
| --- | --- | --- |
| 单机本地（非容器） | [deploy/cli/](local/) | `./deploy/cli/install.sh` + `magi local start` |
| k8s 单机（dev 模式） | [deploy/k8s-dev/](k8s-dev/) | `./deploy/k8s-dev/bootstrap-k8s-dev.sh` |
| k8s 生产（已有集群） | [deploy/k8s/](k8s/) | `./deploy/k8s/bootstrap-k8s.sh` |

下面这张决策树帮你选路径：

```text
                    ┌─ 我只想跑一个本地 MAGI 试试 ─── deploy/cli/
                    │
你想做什么？ ────────┼─ 我在改 k8s 清单 / 想用 Vite HMR ── deploy/k8s-dev/
                    │
                    └─ 我要把 MAGI 部署到现有集群 ─────── deploy/k8s/
```

## 三种方式的差异

|  | 本地（非容器） | k8s-dev（kind） | k8s 生产 |
| --- | --- | --- | --- |
| 容器 | 否 | 是（kind） | 是 |
| 运行时 | `magi local start`（exec 替换为 `magi runtime`） | Pod（`magi:dev` + 源码挂载） | Pod（`magi:0.1.0`） |
| 进程模型 | 每个 MAGI 独立 OS 进程 | 每个 MAGI 独立 Pod | 每个 MAGI 独立 Pod |
| 后端热重载 | 否 | 是（Uvicorn + Vite HMR） | 否 |
| 源码映射 | 否 | 是（`/mnt/magi/magi`） | 否 |
| WebUI 端口 | 42069（Adam）/ 42070+（EVA） | 42069（kind NodePort 30069） | 42069（需 port-forward） |
| 持久化 | `~/.magi/MAGIC/<slug>/workspace/` | `~/.magi/MAGIC/eva-000/workspace/`（hostPath） | PVC `/workspace` |
| 注册成服务 | 是（每 MAGI 独立 systemd unit） | 否 | 否 |
| 唯一前置 | Python 3.12+ | Docker + kind | 现有 k8s 集群 |

## 共享文件

```text
deploy/
├── Dockerfile              # 生产镜像（k8s + k8s-dev 都会用到）
├── Dockerfile.dev          # dev 镜像（仅 k8s-dev）
├── entrypoint.dev.sh       # dev 容器入口（仅 k8s-dev）
└── .tools/                 # 固定工具（kind 等）
```

`Dockerfile` 是 k8s 与 k8s-dev 共用的生产镜像；`Dockerfile.dev` 只
在 k8s-dev 模式下用——本地非容器路径**不**使用任何 Docker。

## 共享意图

三种方式提供同一个**应用抽象**：

- 每个 MAGI 的私有 SQLite（`workspace/memories/magi.db`）+ 工作区；
- 每个 MAGIS 的独立数据库（K8s: PostgreSQL，本地: SQLite）+ 公共工作区；
- 一个 `magi-webui` 入口作为唯一浏览器界面。

路径解析由环境变量驱动：
- K8s 容器内：`MAGI_WORKSPACE_DIR` 指向 PVC 挂载点
- 本地进程：`HOST_WORKSPACE_DIR` + `MAGI_RUNTIME_ID` + `MAGI_NAME`

不存在硬编码的 `/workspace` 路径。`magi/launcher/paths.py` 是唯一暴露
路径布局的地方。其余代码只读环境变量，不假设任何具体 mount 类型。
