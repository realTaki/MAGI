# 生产持久化与状态边界方案（未实施）

> 状态：设计方案，尚未改动生产运行代码、Kubernetes 清单或数据模型。
>
> 本文定义 MAGI 从本地 kind 开发环境走向生产集群时的持久化边界。它不改变
> 「每个 MAGI Citizen 拥有独立运行时与工作区」这一产品模型；改变的是持久化
> 的后端和运维方式。

## 结论

生产环境**不使用宿主机目录映射或 `hostPath`**。但不应因此删除每个 MAGI 的
持久化工作区，而应改为由 Kubernetes CSI 后端提供、每个 MAGI 独占的 PVC。

```text
本地开发（仅 dev overlay）
宿主机源码 / workspace ── hostPath ──> kind Pod

生产环境
不可变镜像 ────────────────────────────> MAGI Pod
每个 MAGI 的 CSI PVC ────────────────> /workspace
PostgreSQL ───────────────────────────> 事务性状态
对象存储 ─────────────────────────────> 附件、归档与备份
Secret Manager ──────────────────────> Provider 凭据与控制密钥
```

当前仓库已具备这条路径的第一部分：`deploy/k8s/base` 为 `/workspace`
声明 PVC，EVE Orchestrator 也会创建独立的 workspace PVC。`hostPath` 仅应
存在于 `overlays/dev-eva00`，用于热加载和本机人工调试，绝不可进入生产 overlay。

## 数据分层

| 数据 | 当前形态 | 生产目标 | 说明 |
|---|---|---|---|
| 工作区文件 | `/workspace`：SOUL、skills、受控文件、SQLite 过渡期数据 | 每个 MAGI 一个 CSI PVC | 保留 POSIX 文件系统语义，支持 Agent 的安全文件编辑工具。 |
| 事务性状态 | SQLite：组织、联系人、会话、任务、审计、运行状态 | PostgreSQL | 提供可靠备份、并发控制和未来横向扩展能力。 |
| 大对象 | 尚未独立分层 | S3/MinIO 等对象存储 | 用于附件、导出、模型产物、workspace 归档和备份。 |
| 密钥 | Kubernetes Secret / 运行时配置 | 外部 Secret Manager + 短期凭据 | 包括 Provider API Key、控制面密钥和机器人凭据。 |
| 临时计算文件 | 工作区内可能混存 | `emptyDir` 或临时卷 | 可丢弃的下载、缓存和中间结果不进入备份。 |

不要将 S3/MinIO 直接 FUSE 挂载为 `/workspace`。MAGI 的文件编辑、原子写入和
skills 扫描都依赖正常的文件系统语义；对象存储应承载大对象与备份，而非替代
可编辑工作区。

## 阶段一：单副本生产化

此阶段不要求立刻迁移 SQLite。每个 MAGI 仍然只有一个 Pod，使用一个
`ReadWriteOnce` PVC，因而不会出现多个 Pod 同时写同一 SQLite 文件的情况。

1. 生产镜像以不可变 digest 发布；不挂载 `/app/magi`，不启用 Uvicorn/Vite
   reload。
2. 为生产 overlay 显式指定受支持的 `StorageClass`（云盘、Ceph RBD 等 CSI
   后端），而不是依赖宿主机路径或开发集群默认行为。
3. 一个 MAGI Citizen 对应一个命名稳定的 PVC，例如
   `eve-<magic-id>-workspace`；PVC、Deployment、Secret 都标记
   `magis_id` 与 `magic_id`。
4. 保持 `replicas: 1` 与 `Recreate` 策略。当前 SQLite 后端不能支持同一
   workspace 的多副本。
5. 为 PVC 配置定期 VolumeSnapshot；同时将工作区归档写入对象存储，避免只
   依赖单一存储系统。
6. 数据库迁移由受控启动流程或专用 Job 执行，不把多副本竞态留给应用启动。

Kubernetes 的 PVC 是请求持久存储容量与访问模式的标准边界；实际卷由
StorageClass/CSI 供给。[Kubernetes Persistent Volumes 文档](https://kubernetes.io/docs/concepts/storage/persistent-volumes/)

## EVE 生命周期与数据保留

“停止”和“销毁”必须是不同操作：

| 操作 | Deployment | PVC / workspace | Provider Secret | 数据语义 |
|---|---|---|---|---|
| 停止 EVE | 缩容至 0 | 保留 | 保留或撤销短期 token | 可恢复，不丢失记忆与配置。 |
| 恢复 EVE | 缩容至 1 | 复用原 PVC | 重新注入凭据 | 同一 MAGI 在原工作区继续运行。 |
| 归档 EVE | 缩容至 0 | 创建快照并归档到对象存储 | 撤销 | 保留可审计、可恢复的历史。 |
| 销毁 EVE | 删除 | 仅在保留期结束后删除 PVC/快照 | 立即撤销与删除 | 不可逆，必须显式确认并写审计日志。 |

生产环境不应把“删除 Deployment”视为“已安全删除数据”。PVC 的回收策略、卷
快照、对象存储保留期和数据库记录必须一起构成删除工作流。Orchestrator 应持续
以数据库中的期望状态为准进行 reconcile，而不是由 Adam 直接持有 Kubernetes
高权限或执行宿主机命令。

## 阶段二：将核心状态迁移到 PostgreSQL

SQLite 适合单节点、单副本 MAGI 的早期生产阶段，但不适合作为可弹性调度和高可用
系统的核心状态库。迁移完成后：

- 组织树、MAGIC/EVE 运行状态、联系人、会话、任务、审计和 token 用量进入
  PostgreSQL；
- 每个 MAGI 的数据通过 `magic_id` 作用域隔离，必要时进一步使用独立
  database/schema 或 Row-Level Security；
- PostgreSQL 做自动备份与 point-in-time recovery；
- `/workspace` 保留为可编辑的人格与技能工作区，不再是唯一的事实来源；
- Pod 可在节点故障后重新调度，而不会依赖原宿主机。

是否使用 StatefulSet 取决于一个 MAGI 是否需要稳定 Pod 身份、稳定网络身份或
有序扩缩容。对于“一个 MAGI 对应一个显式命名 PVC、单副本 Deployment”的近期
模型，当前 Deployment 足够；当出现一组有序、持久副本时再引入 StatefulSet。
[Kubernetes StatefulSet 文档](https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/)

## 密钥与权限

Provider API Key、`MAGI_CONTROL_SECRET` 和机器人 token 不进入：

- Git 仓库；
- 容器镜像；
- `/workspace`；
- 日志、任务 prompt 或审计正文。

近期可使用 Kubernetes Secret，但集群必须启用 etcd 静态加密、最小 RBAC、按
namespace/MAGIS 边界隔离访问，并限制只有真正需要凭据的容器能挂载它。长期建议
采用云 Secret Manager 或 Vault，并通过 External Secrets / Secret Store CSI
注入短期或可轮换凭据。[Kubernetes Secret 安全建议](https://kubernetes.io/docs/concepts/security/secrets-good-practices/)

控制面继续采用最小权限原则：Adam 只调用内部 Orchestrator API；只有
Orchestrator 的 ServiceAccount 能创建、缩放或删除 EVE 的 Deployment、PVC 与
Secret。生产环境应逐步将现有 HMAC 通道升级为服务身份、网络策略与 mTLS。

## 备份、恢复与演练

每个生产 MAGIS 至少需要：

1. PostgreSQL 的定期全量备份与 PITR；
2. 每个 workspace PVC 的快照策略；
3. workspace 关键文件和附件到对象存储的版本化归档；
4. 每个备份对应的 MAGI、MAGIS、MAGIC、镜像 digest、数据库迁移版本元数据；
5. 定期恢复演练：从备份恢复一个隔离的 EVE，并验证 SOUL、skills、会话和
   任务状态。

恢复目标不是只让 Pod 变为 Running，而是让同一 MAGI Citizen 恢复其身份、工作区、
事务性历史及正确的凭据引用。

## 实施顺序

本文不要求现在实施。实际工作建议按以下次序拆分：

1. 新增生产 overlay，CI 阻止 `hostPath`、开发镜像和 reload 配置进入其中。
2. 明确 StorageClass、PVC 标签、快照与删除保留策略。
3. 为 Orchestrator 的停止、归档、销毁引入可审计的状态机。
4. 接入外部 Secret Manager 与凭据轮换。
5. 设计并实施 PostgreSQL 迁移、备份和恢复演练。
6. 在确有多副本需求时，再评估 StatefulSet、连接池、迁移锁与高可用策略。

