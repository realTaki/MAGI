# MAGI Kubernetes 部署

这里是 MAGI 的 Kubernetes 部署文件，与 `deploy/docker-compose/*.yml` 分开维护。

当前清单支持：

- 一个 Adam manager 节点；
- 每个 MAGI 一个独立 Deployment；
- 每个 MAGI 一个独立 PVC，挂载到容器 `/workspace`；
- 源码保留在不可变镜像内（`/app/magi`），不会挂载到 `/workspace`；
- Adam 通过 ClusterIP Service 提供 WebUI；
- EVE 使用 Telegram 时不创建 HTTP Service；
- SQLite 默认单副本运行，避免多个 Pod 同时写同一个数据库。

## 目录结构

```text
deploy/k8s/
├── namespace.yaml
├── base/
│   ├── configmap.yaml
│   ├── deployment.yaml
│   ├── kustomization.yaml
│   ├── pvc.yaml
│   ├── service.yaml
│   └── serviceaccount.yaml
├── overlays/
│   ├── adam/
│   │   ├── ingress.example.yaml
│   │   ├── kustomization.yaml
│   │   └── patch-config.yaml
│   └── eve-example/
│       ├── kustomization.yaml
│       ├── patch-config.yaml
│       └── patch-delete-service.yaml
├── secrets/
│   ├── adam-magi-secrets.example.yaml
│   └── eve-example-magi-secrets.example.yaml
└── README.md
```

`base` 是公共节点模板；overlay 通过 name prefix 为每个节点生成独立的
Deployment、PVC、ConfigMap 和 Service 名称。

## 前置条件

需要：

- Kubernetes 集群；
- `kubectl`，并且支持 `kubectl apply -k`；
- 一个可被集群拉取的 MAGI 镜像仓库；
- 集群有默认 StorageClass，或者在 overlay 中指定 `storageClassName`。

当前应用默认使用 SQLite，因此必须保持单副本。后续切换到 PostgreSQL 后，
才适合重新设计多副本和水平扩展。

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

## 2. 部署 Adam

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

## 3. Secret 和 Telegram

WebUI-only Adam 不需要 Secret。清单中的 Secret 引用是 optional 的，因此没有
Secret 时 Pod 也能启动。

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
      deploy/k8s/overlays/eve-alice
```

然后修改：

1. `eve-alice/kustomization.yaml`：

   ```yaml
   namePrefix: eve-alice-
   ```

2. `eve-alice/patch-config.yaml`：

   ```yaml
   data:
     MAGI_NODE_ROLE: eve
     MAGI_ADAM_URL: http://adam-magi:42069
   ```

3. 创建 EVE 的 Telegram Secret。因为 overlay 使用了 `eve-alice-` 前缀，
   Secret 名称也要带前缀：

   ```bash
   kubectl -n magi create secret generic eve-alice-magi-secrets \
     --from-literal=MAGI_BOT_TOKEN='alice-eves-bot-token' \
     --from-literal=MAGI_SHARED_SECRET='same-adam-shared-secret'
   ```

4. 预览并部署：

   ```bash
   kubectl kustomize deploy/k8s/overlays/eve-alice
   kubectl apply -k deploy/k8s/overlays/eve-alice
   ```

每个 EVE 都会得到自己的资源，例如：

```text
Deployment: eve-alice-magi-node
PVC:       eve-alice-magi-workspace
ConfigMap:  eve-alice-magi-config
Secret:     eve-alice-magi-secrets
```

宿主机目录映射的等价概念是：

```text
Adam       → adam-magi-workspace PVC → /workspace
EVE Alice  → eve-alice-magi-workspace PVC → /workspace
EVE Bob    → eve-bob-magi-workspace PVC → /workspace
```

各个 MAGI 的 `/workspace` 相互隔离，不共享 SQLite、SOUL、skills 或 session。

## 5. 持久化布局

Kubernetes 中不再使用 Docker Compose 的 host bind mount：

```text
Compose:
宿主机目录/Adam:/workspace

Kubernetes:
PVC → /workspace
```

容器内布局保持一致：

```text
/workspace/SOUL.md
/workspace/memories/magi.db
/workspace/memories/sessions/
/workspace/skills/
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
