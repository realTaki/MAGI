# MAGI 部署入口

MAGI 当前支持两种部署方式：

```text
deploy/
├── docker-compose.yml       # 单机/本地生产模式
├── docker-compose.dev.yml   # 本地开发模式覆盖
├── Dockerfile               # 生产镜像
├── Dockerfile.dev           # 开发镜像
└── k8s/                     # Kubernetes + Kustomize 清单
```

## Docker Compose

现有 Compose 文件保留在 `deploy/` 根目录，兼容已有命令：

```bash
docker compose -f deploy/docker-compose.yml up -d
```

开发模式：

```bash
docker compose \
  -f deploy/docker-compose.yml \
  -f deploy/docker-compose.dev.yml \
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
