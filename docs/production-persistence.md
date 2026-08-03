# 生产持久化与状态边界

> 状态：第一阶段已实现。每个 MAGI 的私有状态与每个 MAGIS 的组织状态已经
> 分离；高可用 PostgreSQL、对象存储归档、外部 Secret Manager 和恢复演练仍是后续工作。

本文说明生产部署的持久化边界。完整的当前资源和启动顺序见
[MAGI 与 MAGIS 的存储边界](magi-magis-storage.md)。

## 当前架构

生产环境不使用宿主机目录映射或 `hostPath`。只有本地 kind 开发 overlay 可以将
源码和工作区映射进容器以支持热加载。

```text
每个 MAGI
  私有 PVC ────────────────> /workspace
  私有 SQLite ─────────────> 记忆、会话、任务、本地设置

每个 MAGIS
  PostgreSQL ──────────────> 组织树、MAGI、角色、直接归属、instructions、provider、运行状态
  公共 PVC ────────────────> /magis

Kubernetes Secret
  MAGIS_DATABASE_URL ──────> 直属 MAGIS PostgreSQL 的连接串
```

一个 MAGI 只拥有一个**直接** MAGIS Membership，因此运行时只读取一个
`MAGIS_DATABASE_URL`，也只挂载一个 `/magis`。ADAM 可以管理所在 MAGIS 的子树，
但不会因管理权限而读取子 MAGIS 的 instructions 或挂载其公共 PVC。

## 数据边界

| 数据 | 权威存储 | 挂载/访问者 | 说明 |
|---|---|---|---|
| 私人记忆、会话、任务、本地设置 | MAGI 私有 SQLite | 该 MAGI | 单副本 PVC，保留正常 POSIX 文件语义。 |
| SOUL、私有 skills 与私人文件 | MAGI 私有 PVC `/workspace` | 该 MAGI | 不在 MAGI 之间共享。 |
| MAGIS 树、MAGI、角色、直接 Membership | 直属 MAGIS PostgreSQL | 该 MAGIS 的直接成员与受限控制面 | 一个 MAGI 只能有一个直接 Membership。 |
| 团队、角色、个人 instruction 与 provider 配置 | 直属 MAGIS PostgreSQL | 对应 MAGI 运行时 | 运行容器从数据库读取，不通过环境变量接收内容。 |
| 团队共享文件 | MAGIS 公共 PVC `/magis` | 该 MAGIS 的直接成员 | Kubernetes volume mount 是访问边界。 |
| 大对象、归档与备份 | 尚未实现 | — | 后续使用对象存储，而非 FUSE 替代工作区。 |

私有 SQLite 的 Alembic baseline 仍含组织表的历史 DDL，目的是兼容旧开发数据库；
它们不是 Kubernetes 运行时的组织事实来源。新组织功能必须通过
`magi.db.magis` 访问 PostgreSQL。

## Kubernetes 资源与生命周期

Genesis 在部署时拥有一个 PostgreSQL Deployment、数据库 PVC、公共工作区 PVC 和
数据库 Secret。创建子 MAGIS 时，受限 orchestrator 创建同构资源。启动 MAGI 时，
控制面先把直接 Membership、角色、instructions 和 provider 配置投影至目标 MAGIS
PostgreSQL，然后才创建该 MAGI 的 Deployment 和私有 PVC。

停止和删除是不同操作：

| 操作 | Deployment | 私有 PVC | MAGIS PostgreSQL / 公共 PVC |
|---|---|---|---|
| 停止 | 缩容至 0 | 保留 | 保留 |
| 恢复 | 缩容至 1 | 复用 | 复用直属 MAGIS 资源 |
| 删除 MAGI | 删除 | 删除 | 保留（可能仍被其他成员使用） |
| 删除 MAGIS | 尚未提供自动删除流程 | — | 必须先处理成员、子树、备份与保留期 |

生产 overlay 必须保持每个私有 SQLite PVC 对应一个 Pod 和 `Recreate` 策略；不得为
同一 MAGI 扩成多副本。

## 密钥与权限

`MAGIS_DATABASE_URL` 和 PostgreSQL 密码来自 Kubernetes Secret。provider API key
保存在直属 MAGIS PostgreSQL，运行容器从该数据库解析，不作为 Deployment 环境变量
注入。真实密码、控制面密钥、机器人 token、数据库文件和 kubeconfig 都不得提交 Git、
写进 ConfigMap 或日志。

ADAM 不获得 Docker socket 或 Kubernetes API token。只有 orchestrator 的
ServiceAccount 能创建、缩放和删除受限的 Deployment、PVC、Service、Secret 与
PostgreSQL Deployment；ADAM 仅调用经 HMAC 认证的内部 API。

## 后续生产化工作

1. 为 PostgreSQL 增加备份、PITR 与恢复演练；为私有/公共 PVC 配置快照和保留策略。
2. 将 PostgreSQL 从单副本 Deployment 升级为受管或高可用服务，并为 schema migration
   引入显式锁与版本管理。
3. 将大对象、导出与工作区归档放入 S3/MinIO 等对象存储；不要把对象存储 FUSE 挂为
   `/workspace`。
4. 引入外部 Secret Manager、轮换与网络策略/mTLS。
5. 审计并实现 MAGIS 删除、归档和跨树迁移的显式工作流。
