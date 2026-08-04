# MAGI 部署

`deploy/` 下提供**三种**并行部署方式，按工作场景二选一：

| 场景 | 路径 | 入口 |
| --- | --- | --- |
| 单机本地（openclaw 风格） | [deploy/local/](local/) | `./deploy/local/install.sh` + `magi local start` |
| k8s 单机（dev 模式） | [deploy/k8s-dev/](k8s-dev/) | `./deploy/k8s-dev/bootstrap-k8s-dev.sh` |
| k8s 生产（已有集群） | [deploy/k8s/](k8s/) | `./deploy/k8s/bootstrap-k8s.sh` |

下面这张决策树帮你选路径：

```text
                    ┌─ 我只想跑一个本地 MAGI 试试 ─── deploy/local/
                    │
你想做什么？ ────────┼─ 我在改 k8s 清单 / 想用 Vite HMR ── deploy/k8s-dev/
                    │
                    └─ 我要把 MAGI 部署到现有集群 ─────── deploy/k8s/
```

## 三种方式的差异

|  | 本地（openclaw） | k8s-dev（kind） | k8s 生产 |
| --- | --- | --- | --- |
| 容器 | 否 | 是（kind） | 是 |
| 运行时 | 直接 `magi local start` | Pod（`magi:dev` + 源码挂载） | Pod（`magi:0.1.0`） |
| 后端热重载 | 否 | 是（Uvicorn + Vite HMR） | 否 |
| 源码映射 | 否 | 是（`/mnt/magi/magi`） | 否 |
| WebUI 端口 | 42069 | 42069（kind NodePort 30069） | 42069（需 port-forward） |
| 持久化 | `~/.magi` 或 `~/Documents/.magi` | 宿主 `workspace/` | PVC |
| 注册成服务 | 是（Linux systemd user unit） | 否 | 否 |
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
在 k8s-dev 模式下用——本地 openclaw 路径**不**使用任何 Docker。

## 共享意图

三种方式提供同一个**应用抽象**：

- 每个 MAGI 的私有 SQLite + Persistent 工作区；
- 每个 MAGIS 的 PostgreSQL + 公共工作区；
- 一个 MAGI 镜像 + 一组 Kustomize overlay；
- 一个 `magi-webui` ClusterIP Service（或 localhost 端口）作为唯
  一浏览器入口。

`magi/launcher/paths.py` 是唯一暴露本地路径布局的地方。其余
代码读 `MAGI_WORKSPACE_DIR`（容器）或 `MAGI_DATA_ROOT`（本地），
不假设任何具体 mount 类型。
