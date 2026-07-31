# MAGI 与 MAGIS 的存储边界

## 两个持久化层

每个 MAGI 有一个私有工作区和 SQLite：

```text
workspace/MAGIC/<magi-name>/
├── memories/magi.db       # 私人记忆、会话、联系人、任务与本地设置
├── skills/
└── SOUL.md
```

每个 MAGIS 有两个公共资源：独立 PostgreSQL 和公共工作区 PVC。

```text
workspace/MAGIS/<magis-name>/  # 团队共享文件；生产环境对应独立 PVC
```

PostgreSQL 保存组织事实：MAGIS 树、MAGI 注册表、直接 Membership、角色、团队/角色/个人 instructions、provider 配置和运行时状态。私有 SQLite 不保存这些组织事实，也不会成为另一份 MAGIS 数据库。

## 直接归属与管理范围

一个 MAGI 只能有一个直接 MAGIS Membership。该 Membership 决定：

- 它的角色与实际加载的 team/role instruction；
- 它启动时连接的公共数据库；
- 它挂载的唯一 MAGIS 公共 PVC。

Adam 对所在 MAGIS 的整个子树拥有管理权限，但这不是 Membership：不会加载子 MAGIS instructions，也不会挂载子 MAGIS 公共 PVC。子 MAGIS 的成员同样不会继承父 MAGIS 的 instructions。

## Kubernetes 资源

Genesis 使用：

- `magi-magis-1-genesis-db`：PostgreSQL Deployment 和 ClusterIP Service；
- `magi-magis-1-genesis-db-data`：数据库 PVC；
- `magi-magis-1-genesis-workspace`：公共工作区 PVC；
- `magi-magis-1-genesis-db` Secret：`POSTGRES_PASSWORD` 和 `MAGIS_DATABASE_URL`。

复制 `deploy/k8s/secrets/magis-genesis-db.example.yaml` 到不提交 Git 的位置，填入强随机密码后先 apply。`adam` overlay 会把数据库 URL 从该 Secret 注入初始节点；`dev-eva00` overlay 生成仅供本地 kind 使用的开发 Secret。

新 MAGIS 由受限的 orchestrator 创建同构资源。新 MAGI 由 orchestrator 只注入其直属 MAGIS 的 `MAGIS_DATABASE_URL` 和 `MAGIS_ID`，并挂载 `/magis`。它不接收 provider、API Key、角色或 instruction 环境变量。

启动前，控制面会把该 MAGI 的直接 Membership、角色、instructions 与 provider 配置投影到目标 MAGIS PostgreSQL；该投影通过受 HMAC 保护的控制请求写入数据库。运行容器只从数据库读取。修改这些组织配置后，重新启动该 MAGI 会刷新其运行时投影。

## 启动顺序

1. 创建 namespace、Genesis DB Secret、Genesis PostgreSQL/PVC；
2. 等待 Genesis PostgreSQL Ready；
3. 启动初始节点；它在公共数据库创建 Genesis、`eva-00`、保留 Adam/EVE 角色及直接 Membership；
4. onboarding 完成后，Adam 可创建子 MAGIS；控制面为其创建 PostgreSQL 与公共 PVC；
5. 创建 MAGI、将其分配到一个直属 MAGIS、配置 provider 后才可启动容器。

## 安全边界

公共 PVC 的访问由 Kubernetes 是否挂载决定，不以宿主机 `chmod` 作为成员权限系统。公共数据库 URL 必须来自 Kubernetes Secret；生产环境不应将数据库密码放进 ConfigMap、命令行或 Git。
