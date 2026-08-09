# MAGI Kubernetes 部署

这是 **生产** k8s 部署入口。`deploy/k8s/` 下的所有 manifest
与一个真实存在的 k8s 集群（EKS、GKE、AKS、自建、k3s 等）配
合使用，**不** 包含源码映射——容器内 `/app/magi` 由镜像提供，
不挂载宿主机目录。

`deploy/` 共有三种部署方式：

| 目录 | 形态 | 用途 |
| --- | --- | --- |
| [deploy/cli/](../cli/) | 单机非容器（CLI） | openclaw 风格一键本地启动 |
| [deploy/k8s-dev/](../k8s-dev/) | k8s 单机（kind dev） | 调试 k8s 模块化方案 |
| `deploy/k8s/` ← 当前 | k8s 生产 | 把现有集群当生产环境使用 |

kind dev 集群在 `deploy/k8s-dev/` 单独维护；它仍复用本目录的
`base/` 节点模板，但额外叠加了：

- `kind.yaml` 单节点 kind 配置；
- `control-dev/` 把 WebUI 切到 Vite HMR + 源码 `/mnt/magi` 挂载；
- `overlays/dev-eva00/` 把 magi-node 切到 `magi:dev` 镜像 + 源码挂载。

请把上面的本地开发场景放到 `deploy/k8s-dev/`，本目录只保留生产
配置。

## 目录结构

```text
deploy/k8s/
├── README.md                          ← this file
├── bootstrap-k8s.sh                   生产部署脚本
├── namespace.yaml
├── base/
│   ├── configmap.yaml
│   ├── deployment.yaml
│   ├── kustomization.yaml
│   ├── magis-genesis.yaml
│   ├── pvc.yaml
│   ├── service.yaml
│   └── serviceaccount.yaml
├── control/                           生产 orchestrator + 唯一 WebUI
│   ├── configmap.yaml
│   ├── deployment.yaml                orchestrator
│   ├── kustomization.yaml
│   ├── role.yaml
│   ├── rolebinding.yaml
│   ├── service.yaml
│   ├── serviceaccount.yaml
│   ├── webui-deployment.yaml
│   └── webui-service.yaml
├── overlays/
│   ├── adam/
│   │   ├── ingress.example.yaml
│   │   ├── kustomization.yaml
│   │   ├── patch-config.yaml
│   │   └── patch-magis-genesis.yaml
│   └── eva-example/
│       ├── kustomization.yaml
│       ├── patch-config.yaml
│       └── patch-delete-service.yaml
└── secrets/
    ├── adam-magi-secrets.example.yaml
    ├── eva-example-magi-secrets.example.yaml
    └── magis-genesis-db.example.yaml
```

`base` 是初始节点模板，并声明 Genesis 的 PostgreSQL 和公共工作区。`control/` 同时
部署 orchestrator 与唯一的 `magi-webui` 控制台；两者和所有 MAGI 使用同一个镜像，
只是命令不同。普通运行时
由 orchestrator 按 MAGI/MAGIS ID 创建稳定命名的资源；不要依赖 Kustomize name prefix
推导这些名称。

## 启动模型

每个节点先由一次性 init container 执行 `magi init`，只负责 provision
Genesis、`eva-000`、schema、默认资源和控制面记录。主容器随后仅执行
`magi node run --foreground`：它读取并校验已持久化的 RuntimeSpec，绝不
建表、创建目录或生成密钥。`magi-webui` 是独立控制面进程，使用
`magi webui run --foreground`。

因此 Kubernetes 不应把 `node run` 当成初始化命令，也不应以旧根目录
`magi.db` 启动。节点私有数据库的唯一位置是
`/MAGI_Citizens/<name>/memories/magi.db`。

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
- PVC 在容器根目录 `/` 挂载，持久化数据位于
  `/MAGI_Citizens/<name>/memories/magi.db`；
- 不需要在 Pod 内运行 Vite。

## 2. 创建 Genesis 数据库 Secret 并部署 ADAM

Genesis PostgreSQL 是初始 MAGIS 的必需依赖。先复制示例到一个不提交 Git 的位置，
填入强随机密码，然后创建 Secret：

```bash
cp deploy/k8s/secrets/magis-genesis-db.example.yaml /tmp/magis-genesis-db.yaml
# 编辑 /tmp/magis-genesis-db.yaml 中的 POSTGRES_PASSWORD 和 MAGIS_DATABASE_URL
kubectl -n magi apply -f /tmp/magis-genesis-db.yaml
```

`MAGIS_DATABASE_URL` 必须指向 `magi-magis-1-genesis-db:5432/magis_1`。
init container 从这个 Secret 获得连接串，并在监听任意 Runtime 端口前
provision Genesis 与 `eva-000`。

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
kubectl -n magi logs -f deploy/magi-node
kubectl -n magi logs -f deploy/magi-webui
```

本地访问 WebUI 可以使用 port-forward：

```bash
kubectl -n magi port-forward svc/magi-webui 42069:42069
```

然后打开：

```text
http://127.0.0.1:42069/
```

统一 WebUI 的集群内地址是：

```text
http://magi-webui.magi.svc.cluster.local:42069
```

短 Service 名在同一 namespace 内也可以使用：

```text
http://magi-webui:42069
```

## 3. Telegram Secret（可选）

WebUI-only ADAM 不需要 Telegram Secret；Genesis 数据库 Secret 则是上一节的必需项。
清单中的 Telegram Secret 引用是 optional 的，因此没有它 Pod 仍能启动。

如果 ADAM 同时启用 Telegram,推荐通过命令行创建 Secret,而不是把真实密钥写
进 Git:

```bash
kubectl -n magi create secret generic adam-magi-secrets \
  --from-literal=MAGI_BOT_TOKEN='replace-with-real-token' \
  --from-literal=MAGI_SHARED_SECRET='replace-with-long-random-secret'
```

ADAM 默认只跑 webui 通道。TG bot 在 `save-bot` 步骤由 onboarding 拉起
(daemon 跑在 webui worker 进程里),不需要把 `MAGI_CHANNELS` 预设成
`webui,telegram` —— settings DB 里 `telegram.bot_token` 一旦写入,
节点启动时就会自动把 telegram 通道加进来。

也可以参考但不要直接提交真实凭据：

```text
deploy/k8s/secrets/adam-magi-secrets.example.yaml
```

## 4. 部署一个 EVA

`overlays/eva-example` 是一个可复制的 EVA 模板。它会：

- 设置 `MAGI_NODE_ROLE=eva`；
- 创建独立的 Deployment 和 PVC；
- 删除 EVA 的 HTTP Service，因为 Telegram polling 不需要入站 HTTP；
- 将 `MAGI_ADAM_URL` 指向 `adam-magi:42069`。

EVA 的通道由 `settings.channels.enabled` 控制（telegram 在 onboarding 的
save-bot 写入 bot token 后自动启用），无需在启动时预设 `MAGI_CHANNELS`。

先复制目录：

```bash
cp -R deploy/k8s/overlays/eva-example \
      deploy/k8s/overlays/eva-eva00
```

然后修改：

1. `eva-eva00/kustomization.yaml`：

   ```yaml
   namePrefix: eva-eva00-
   ```

2. `eva-eva00/patch-config.yaml`：

   ```yaml
   data:
     MAGI_NODE_ROLE: eva
     MAGI_ADAM_URL: http://adam-magi:42069
   ```

3. 创建 EVA 的 Telegram Secret。因为 overlay 使用了 `eva-eva00-` 前缀，
   Secret 名称也要带前缀：

   ```bash
   kubectl -n magi create secret generic eva-eva00-magi-secrets \
     --from-literal=MAGI_BOT_TOKEN='eva00-evas-bot-token' \
     --from-literal=MAGI_SHARED_SECRET='same-adam-shared-secret'
   ```

4. 预览并部署：

   ```bash
   kubectl kustomize deploy/k8s/overlays/eva-eva00
   kubectl apply -k deploy/k8s/overlays/eva-eva00
   ```

每个由 orchestrator 启动的 MAGI 都会得到自己的资源，例如：

```text
Deployment: eva-eva00-magi-node
PVC:       eva-eva00-magi-workspace
ConfigMap:  eva-eva00-magi-config
MAGIS DB:   magi-magis-<magis-id>-<name>-db（共享，不是每个 MAGI 一个）
```

宿主机目录映射的等价概念是：

```text
ADAM       → adam-magi-workspace PVC → /workspace
EVA Eva00  → eva-eva00-magi-workspace PVC → /workspace
EVA Bob    → eva-bob-magi-workspace PVC → /workspace
```

各个 MAGI 的 `/workspace` 相互隔离，不共享 SQLite、SOUL、skills 或 session。
它们只挂载直属 MAGIS 的 `/magis` 公共 PVC；ADAM 对子 MAGIS 的管理权不意味着
它会挂载子 MAGIS 的公共工作区。

## 5. 持久化布局

Kubernetes Pod **不传** `HOST_WORKSPACE_DIR`。Pod 启动时
`magi.startup.paths` 通过 `KUBERNETES_SERVICE_HOST` 检测 K8s 模式，
默认 `HOST_WORKSPACE_DIR=/`；PVC 挂载到容器根 `/`，workspace 推导为
`/MAGI_Citizens/<name>`。非生产部署（`deploy/cli/` 单机 CLI、
`deploy/k8s-dev/` kind dev）虽然底层是宿主目录，但应用看到的目录树与
生产 PVC 完全一致：

```text
# 生产 (K8s)
PVC /                                               per-MAGI 私有 (auto-default HOST_WORKSPACE_DIR=/)
PVC /magis                                          直属 MAGIS 公共

# CLI 单机 (deploy/cli/)
~/.magi/MAGI_Citizens/<slug>/workspace/             per-MAGI 私有
~/.magi/MAGI_Societies/<magis_id>-<slug>/           直属 MAGIS 公共

# k8s-dev (deploy/k8s-dev/)
~/.magi/MAGI_Citizens/<slug>/workspace/             per-MAGI 私有 (hostPath)
~/.magi/MAGI_Societies/<magis_id>-<slug>/           直属 MAGIS 公共 (hostPath)
```

容器内布局保持一致：

```text
/workspace/SOUL.md
/workspace/memories/magi.db
/workspace/memories/sessions/
/workspace/skills/
/magis/                         # 直属 MAGIS 的团队共享文件
```

这样可以让应用代码不感知底层是 bind mount 还是 PVC。路径解析由环境变量驱动，
不存在硬编码的 `/workspace` fallback。

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

修改域名后，将它加入 ADAM overlay 的 `resources`，再执行：

```bash
kubectl apply -k deploy/k8s/overlays/adam
```

不建议默认把 Ingress 清单直接启用，因为不同集群的 Ingress Controller、TLS
和域名策略不同。

## 当前边界

当前 Kubernetes 部署是“一个 ADAM + 手工复制的多个 EVA overlay”。ADAM WebUI
还不会自动创建 Kubernetes Deployment；未来 C6 可以让 ADAM 通过 Kubernetes
API 或一个受限的 operator/controller 来创建和回收 EVA。届时不应直接给 MAGI
Pod 授予任意 Kubernetes 管理权限，而应使用最小权限的专用 controller。
