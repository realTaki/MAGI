# MAGI 部署入口

MAGI 当前支持两种部署方式：

```text
deploy/
├── Dockerfile               # 生产镜像
├── Dockerfile.dev           # 开发镜像
├── entrypoint.dev.sh        # dev entrypoint (bind-mounted)
├── docker-compose/
│   ├── docker-compose.yml      # 单机/本地生产模式
│   └── docker-compose.dev.yml  # 本地开发模式覆盖
└── k8s/                     # Kubernetes + Kustomize 清单
```

Compose 文件单独成目录，跟 `k8s/` 平行。Dockerfile、entrypoint、README
留在 `deploy/` 根。

## Docker Compose

所有变量在 `deploy/.env` 中配置（模板：`deploy/.env.example`）。
容器内的 workspace 固定为 ``/workspace``，宿主机的
``MAGI_WORKSPACE_DIR`` 仅在 compose 卷挂载差值时使用。

```bash
# 复制并编辑环境变量模板
cp deploy/.env.example deploy/.env
# 编辑 deploy/.env — 设置你的宿主机 workspace 绝对路径
# 生产模式
docker compose \
  --env-file deploy/.env \
  -f deploy/docker-compose/docker-compose.yml \
  up -d
```

开发模式：

```bash
docker compose \
  --env-file deploy/.env \
  -f deploy/docker-compose/docker-compose.yml \
  -f deploy/docker-compose/docker-compose.dev.yml \
  up -d
```

## Kubernetes

Kubernetes 文件全部位于：

```text
deploy/k8s/
```

使用说明见：

```text
deploy/k8s/README.md
```

### Genesis 一键启动

本地 Kubernetes 开发体验（宿主机需要 Docker）使用：

```bash
./deploy/bootstrap-local.sh
```

脚本会在 ``deploy/.tools`` 下载锁定版本的 ``kind`` / ``kubectl``，创建本地
集群、构建镜像，并启动 Genesis Alice 与受限的 ``magi-orchestrator``。不会写入
系统路径或使用 sudo。Alice 使用 ``workspace/alice``，并将本仓库源码挂载到 Pod：
Python 由 Uvicorn reload，WebUI 由 Vite HMR 热更新。该 overlay 只适用于本地 kind，
不能用于远程或生产 Kubernetes 集群。

已有 Kubernetes 集群时使用：

```bash
MAGI_IMAGE=registry.example.com/your-team/magi:0.1.0 \
  ./deploy/bootstrap-k8s.sh
```

首次 Adam 启动会在自己的 workspace 中自举 Genesis Council 并绑定为其 Adam。
完成 onboarding 后，管理员可在 WebUI 的「智能体管理」中创建 EVE、设置 provider
和 API key，然后启动或停止 EVE。Adam 不会获得 Kubernetes API token；只有独立的
``magi-orchestrator`` 持有 namespace 限定的最小 RBAC。

两种模式共用 `deploy/Dockerfile` 生产镜像，但持久化方式不同：

```text
Compose     host bind mount → /workspace
Kubernetes  PVC              → /workspace
```

两种模式都不会把源码映射到 Agent 的 `/workspace`。生产镜像中的源码位于
`/app/magi`，Kubernetes 部署只挂载每个 MAGI 自己的 workspace PVC。

## 通道选择

启动节点时**不要**设置 `MAGI_CHANNELS`。webui 始终启动;`telegram`
通道在 onboarding 的 `save-bot` 步骤里自动拉起(daemon 在 webui worker
进程里)。节点重启后,启动逻辑会扫 settings DB 看哪些 channel 已
onboarded,自动恢复 daemon —— 不需要重新设置环境变量。
</content>
