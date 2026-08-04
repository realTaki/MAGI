# MAGI k8s 单机（dev 模式）

这是 **k8s 单机 dev 模式**——通过 kind 在本机启动一个独立的
Kubernetes 集群，然后按**生产相同的 manifest 树**（`deploy/k8s/base/`）
部署一个 dev MAGI。

目的是验证 `deploy/k8s/` 下的清单、Kustomize overlay、RBAC、
orchestrator 边界是否如设计一样工作；同时给后端 + WebUI 提供一个
带 Vite HMR / Uvicorn reload 的现场调试环境。

| 部署 | 路径 | 用途 |
| --- | --- | --- |
| [本地单机（非容器）](../local/) | `deploy/local/` | openclaw 风格一键启动 |
| **k8s 单机（dev）** ← 当前 | `deploy/k8s-dev/` | 调试 k8s 模块化方案 |
| [k8s 生产](../k8s/README.md) | `deploy/k8s/` | 现有集群当生产环境 |

## 快速开始

```bash
# Docker 是唯一宿主机前置依赖
./deploy/k8s-dev/bootstrap-k8s-dev.sh
```

脚本会：

1. 下载并固定 `kind` 与 `kubectl` 到 `deploy/.tools/`，不依赖系统级安装；
2. 创建单节点 kind 集群，把宿主 `MAGI` 仓库挂到节点内 `/mnt/magi`；
3. 构建两套镜像（`magi:0.1.0` 生产、`magi:dev` dev）并加载到 kind；
4. 调用 `deploy/k8s/bootstrap-k8s.sh` 部署：
   - `magi-orchestrator`（生产 control overlay）
   - `magi-webui`（dev `control-dev` overlay：Vite HMR + 源码 `/mnt/magi/magi` 挂载）
   - `magi-node`（dev `overlays/dev-eva00`：源码 `/mnt/magi/magi` 挂载、源码热更新）

启动后访问：

```text
http://127.0.0.1:42069
```

`kind.yaml` 把节点 NodePort 30069 映射到宿主 42069，因此浏览器
直接用 localhost 即可。

## 目录结构

```text
deploy/k8s-dev/
├── README.md                          ← this file
├── bootstrap-k8s-dev.sh               kind + 镜像 + 部署
├── kind.yaml                          单节点 kind 集群配置
├── control-dev/                       Vite HMR + 源码挂载的 WebUI overlay
│   ├── kustomization.yaml
│   ├── patch-webui-service.yaml
│   ├── patch-webui-volumes.json
│   └── patch-webui.yaml
└── overlays/
    └── dev-eva00/                     dev `magi-node` overlay
        ├── kustomization.yaml
        ├── patch-config.yaml
        ├── patch-deployment.yaml
        ├── patch-delete-pvc.yaml
        ├── patch-delete-magis-workspace-pvc.yaml
        ├── patch-probes.json
        ├── patch-service.json
        ├── patch-service.yaml
        └── patch-workspace.json
```

`overlays/dev-eva00/kustomization.yaml` 通过 `../../../k8s/base` 引
用生产 `base/` —— overlay 与生产 manifest 共享同一棵节点模板，
dev 路径只追加差异，**不**复制 base。

## dev 时改了代码，怎么办？

`magi-node` 与 `magi-webui` 都把宿主的 `magi/` 目录以 read-only
hostPath 挂到容器内 `/app/magi`：

- 后端：Uvicorn 监听 `MAGI_RELOAD=1`，保存即重启；
- WebUI：Vite 监听同一路径，保存即 HMR。

仓库根目录的 `magi/`、`deploy/entrypoint.dev.sh` 是被宿主
直接看到的，不要 `chmod -w`。

## 清理

```bash
./deploy/.tools/kind delete cluster --name magi
rm -f .kind-kubeconfig
```

## 不要做的事

- **不要**把 `overlays/dev-eva00` / `control-dev` / `kind.yaml` 套
  到远程或生产集群——它们依赖宿主机 `/mnt/magi` 目录，且
  Dockerfile.dev 的 `runAsNonRoot: false` 是 dev-only 妥协。
- **不要**在 dev 模式下复用生产 `magi-magis-1-genesis-db` Secret
  ——dev 模式自动生成弱密码，仅供本地。
