# MAGI Kubernetes 部署

MAGI 当前**只**通过 Kubernetes 部署。`deploy/` 下的所有
manifest 都假定一个真实的 k8s 集群（kind、minikube、EKS、GKE
等），不再保留 docker-compose 路径。

当前清单支持：

- 一个 Adam manager 节点；
- 每个 MAGI 一个独立 Deployment；
- 每个 MAGI 一个独立 PVC，挂载到容器 `/workspace`；其 MAGIS 另有一个公共 PVC 挂载到 `/magis`；
- 源码保留在不可变镜像内（`/app/magi`），不会挂载到 `/workspace`；
- Adam 通过 ClusterIP Service 提供 WebUI；
- EVE 使用 Telegram 时不创建 HTTP Service；
- 每个 MAGI 的私有 SQLite 仅单副本运行；每个 MAGIS 使用独立 PostgreSQL 保存组织数据。

本地开发另有 ``overlays/dev-eva00``：它仅由 ``../bootstrap-k8s.sh``
配合 kind extraMounts 使用，把宿主机 ``workspace/MAGIC/eva-00`` 映射为
`/workspace`，并把源码映射为 `/app/magi`，以启用 Uvicorn reload
与 Vite HMR。不要将此 overlay 应用于远程/生产集群。

此外，``../bootstrap-k8s.sh`` 会部署 ``magi-orchestrator``。它是唯一可创建 MAGI 的
Deployment/私有 PVC，以及新 MAGIS 的 PostgreSQL、公共 PVC 和数据库 Secret 的组件；
Adam 仅通过带 HMAC 的集群内 API 请求启动/停止。不要为 Adam 挂载 Docker socket 或
Kubernetes ServiceAccount token。

## 目录结构

```text
deploy/k8s/
├── namespace.yaml
├── base/
│   ├── configmap.yaml
│   ├── deployment.yaml
│   ├── kustomization.yaml
│   ├── magis-genesis.yaml
│   ├── pvc.yaml
│   ├── service.yaml
│   └── serviceaccount.yaml
├── overlays/
│   ├── adam/
│   │   ├── ingress.example.yaml
│   │   ├── kustomization.yaml
│   │   ├── patch-config.yaml
│   │   └── patch-magis-genesis.yaml
│   ├── dev-eva00/                 # kind only: source hot reload + host paths
│   └── eve-example/
│       ├── kustomization.yaml
│       ├── patch-config.yaml
│       └── patch-delete-service.yaml
├── secrets/
│   ├── adam-magi-secrets.example.yaml
│   ├── eve-example-magi-secrets.example.yaml
│   └── magis-genesis-db.example.yaml
└── README.md
```

`base` 是初始节点模板，并声明 Genesis 的 PostgreSQL 和公共工作区。普通运行时
由 orchestrator 按 MAGI/MAGIS ID 创建稳定命名的资源；不要依赖 Kustomize name prefix
推导这些名称。

## 前置条件

需要：

- Kubernetes 集群；
- `kubectl`，并且支持 `kubectl apply -k`；
- 一个可被集群拉取的 MAGI 镜像仓库；
- 集群有默认 StorageClass，或者在 overlay 中指定 `storageClassName`。

MAGI 的私人状态使用 SQLite，因此每个 MAGI 私有 PVC 必须保持单副本；MAGIS 的组织数据使用独立 PostgreSQL。完整边界见 [MAGI 与 MAGIS 的存储边界](../../docs/magi-magis-storage.md)。

先创建 namespace（幂等操作）：

```bash
kubectl apply -f deploy/k8s/namespace.yaml
```

## 1. 构建并推送镜像

Kubernetes 节点不能直接使用本机 Docker 镜像，除非你使用 kind/minikube 并显式
加载镜像。生产环境建议先推送到镜像仓库：

```bash
docker build \
  -f deploy/Dockerfile \
  -t registry.example.com/your-team/magi:0.1.0 \
  .

docker push registry.example.com/your-team/magi:0.1.0
```

然后编辑 `deploy/k8s/base/deployment.yaml` 的 `image`，或者在 overlay 中加入：

```yaml
images:
  - name: magi:0.1.0
    newName: registry.example.com/your-team/magi
    newTag: 0.1.0
```

不要在 Kubernetes 中使用 `deploy/Dockerfile.dev`。Kubernetes 应使用生产镜像：

- React 静态文件已经构建到镜像；
- Python 源码位于镜像内的 `/app/magi`；
- 只有 `/workspace` 是持久化挂载；
- 不需要在 Pod 内运行 Vite。

## 2. 创建 Genesis 数据库 Secret 并部署 Adam

Genesis PostgreSQL 是初始 MAGIS 的必需依赖。先复制示例到一个不提交 Git 的位置，
填入强随机密码，然后创建 Secret：

```bash
cp deploy/k8s/secrets/magis-genesis-db.example.yaml /tmp/magis-genesis-db.yaml
# 编辑 /tmp/magis-genesis-db.yaml 中的 POSTGRES_PASSWORD 和 MAGIS_DATABASE_URL
kubectl -n magi apply -f /tmp/magis-genesis-db.yaml
```

`MAGIS_DATABASE_URL` 必须指向 `magi-magis-1-genesis-db:5432/magis_1`。初始 Adam
会从这个 Secret 获得连接串，并在该 PostgreSQL 中创建 Genesis 与 `EVA-00 PROTO TYPE`。

默认 Adam 只挂载 WebUI；`MAGI_CHANNELS` 在 ConfigMap 里**不要**显式设置
（启动逻辑会从 settings DB 自动检测已 onboarded 的通道）：

```yaml
MAGI_NODE_ROLE: adam
```

先预览渲染结果：

```bash
kubectl kustomize deploy/k8s/overlays/adam
```

确认镜像地址、StorageClass 和资源限制后应用：

```bash
kubectl apply -k deploy/k8s/overlays/adam
```

检查状态：

```bash
kubectl -n magi get pods,svc,pvc -l app.kubernetes.io/name=magi
kubectl -n magi logs -f deploy/adam-magi-node
```

本地访问 WebUI 可以使用 port-forward：

```bash
kubectl -n magi port-forward svc/adam-magi 42069:42069
```

然后打开：

```text
http://127.0.0.1:42069/
```

Adam 的集群内地址是：

```text
http://adam-magi.magi.svc.cluster.local:42069
```

短 Service 名在同一 namespace 内也可以使用：

```text
http://adam-magi:42069
```

## 3. Telegram Secret（可选）

WebUI-only Adam 不需要 Telegram Secret；Genesis 数据库 Secret 则是上一节的必需项。
清单中的 Telegram Secret 引用是 optional 的，因此没有它 Pod 仍能启动。

如果 Adam 同时启用 Telegram,推荐通过命令行创建 Secret,而不是把真实密钥写
进 Git:

```bash
kubectl -n magi create secret generic adam-magi-secrets \
  --from-literal=MAGI_BOT_TOKEN='replace-with-real-token' \
  --from-literal=MAGI_SHARED_SECRET='replace-with-long-random-secret'
```

Adam 默认只跑 webui 通道。TG bot 在 `save-bot` 步骤由 onboarding 拉起
(daemon 跑在 webui worker 进程里),不需要把 `MAGI_CHANNELS` 预设成
`webui,telegram` —— settings DB 里 `telegram.bot_token` 一旦写入,
节点启动时就会自动把 telegram 通道加进来。

也可以参考但不要直接提交真实凭据：

```text
deploy/k8s/secrets/adam-magi-secrets.example.yaml
```

## 4. 部署一个 EVE

`overlays/eve-example` 是一个可复制的 EVE 模板。它会：

- 设置 `MAGI_NODE_ROLE=eve`；
- 创建独立的 Deployment 和 PVC；
- 删除 EVE 的 HTTP Service，因为 Telegram polling 不需要入站 HTTP；
- 将 `MAGI_ADAM_URL` 指向 `adam-magi:42069`。

EVE 的通道由 `settings.channels.enabled` 控制（telegram 在 onboarding 的
save-bot 写入 bot token 后自动启用），无需在启动时预设 `MAGI_CHANNELS`。

先复制目录：

```bash
cp -R deploy/k8s/overlays/eve-example \
      deploy/k8s/overlays/eve-eva00
```

然后修改：

1. `eve-eva00/kustomization.yaml`：

   ```yaml
   namePrefix: eve-eva00-
   ```

2. `eve-eva00/patch-config.yaml`：

   ```yaml
   data:
     MAGI_NODE_ROLE: eve
     MAGI_ADAM_URL: http://adam-magi:42069
   ```

3. 创建 EVE 的 Telegram Secret。因为 overlay 使用了 `eve-eva00-` 前缀，
   Secret 名称也要带前缀：

   ```bash
   kubectl -n magi create secret generic eve-eva00-magi-secrets \
     --from-literal=MAGI_BOT_TOKEN='eva00-eves-bot-token' \
     --from-literal=MAGI_SHARED_SECRET='same-adam-shared-secret'
   ```

4. 预览并部署：

   ```bash
   kubectl kustomize deploy/k8s/overlays/eve-eva00
   kubectl apply -k deploy/k8s/overlays/eve-eva00
   ```

每个由 orchestrator 启动的 MAGI 都会得到自己的资源，例如：

```text
Deployment: eve-eva00-magi-node
PVC:       eve-eva00-magi-workspace
ConfigMap:  eve-eva00-magi-config
MAGIS DB:   magi-magis-<magis-id>-<name>-db（共享，不是每个 MAGI 一个）
```

宿主机目录映射的等价概念是：

```text
Adam       → adam-magi-workspace PVC → /workspace
EVE Eva00  → eve-eva00-magi-workspace PVC → /workspace
EVE Bob    → eve-bob-magi-workspace PVC → /workspace
```

各个 MAGI 的 `/workspace` 相互隔离，不共享 SQLite、SOUL、skills 或 session。
它们只挂载直属 MAGIS 的 `/magis` 公共 PVC；Adam 对子 MAGIS 的管理权不意味着
它会挂载子 MAGIS 的公共工作区。

## 5. 持久化布局

Kubernetes 中不再使用 Docker Compose 的 host bind mount：

```text
Compose:
宿主机目录/Adam:/workspace

Kubernetes:
PVC → /workspace
独立 MAGIS PVC → /magis
```

容器内布局保持一致：

```text
/workspace/SOUL.md
/workspace/memories/magi.db
/workspace/memories/sessions/
/workspace/skills/
/magis/                         # 直属 MAGIS 的团队共享文件
```

这样可以让应用代码不感知底层是 bind mount 还是 PVC。

## 6. 访问控制和安全边界

当前 Deployment 做了以下隔离：

- 容器以非 root 用户运行；
- 禁止 privilege escalation；
- 丢弃所有 Linux capabilities；
- 使用 `RuntimeDefault` seccomp profile；
- ServiceAccount 不自动挂载 Kubernetes API token；
- 源码不进入 `/workspace`，而是留在镜像内；
- `/workspace` 只包含该 MAGI 自己的持久化状态。

注意：Agent 的 Bash 工具仍然是容器内 shell。源码保护依赖于生产镜像内的
源码和容器运行时权限；当前 Deployment 没有把 Docker socket、宿主机根目录或
Kubernetes API token 暴露给 Pod。

## 7. 可选 Ingress

如果集群已经安装 Ingress Controller，可以参考：

```text
deploy/k8s/overlays/adam/ingress.example.yaml
```

修改域名后，将它加入 Adam overlay 的 `resources`，再执行：

```bash
kubectl apply -k deploy/k8s/overlays/adam
```

不建议默认把 Ingress 清单直接启用，因为不同集群的 Ingress Controller、TLS
和域名策略不同。

## 当前边界

当前 Kubernetes 部署是“一个 Adam + 手工复制的多个 EVE overlay”。Adam WebUI
还不会自动创建 Kubernetes Deployment；未来 C6 可以让 Adam 通过 Kubernetes
API 或一个受限的 operator/controller 来创建和回收 EVE。届时不应直接给 MAGI
Pod 授予任意 Kubernetes 管理权限，而应使用最小权限的专用 controller。
