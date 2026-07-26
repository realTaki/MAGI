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

Compose 文件**必须**通过 `--env-file deploy/.env` 加载环境变量,因为
``MAGI_WORKSPACE_DIR`` 是 `MAGI_WORKSPACE_DIR:?...` 必填项(强制 operator
在 deploy 时显式指定持久化目录)。`deploy/.env` 在仓库 `.gitignore`
里 —— 第一次部署前 `cp deploy/.env.example deploy/.env` 然后编辑。

```bash
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