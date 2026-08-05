# 统一 WebUI 与 MAGI Runtime API

## 目标

用户只部署一个 `magi` 镜像，但在 Kubernetes 中选择两种服务角色：

```text
magi                 → 一个 MAGI runtime（默认命令）
magi webui           → 全局唯一的 WebUI 控制面
```

`magi-webui` 是浏览器唯一入口。它承载 React SPA、目标绑定的登录会话和内部请求代理，
但不拥有 workspace、本地 SQLite、Bot Token 或用户权限数据。每个 MAGI runtime 不再挂载
SPA，也不直接暴露给浏览器；它只提供 ClusterIP 可达的 Runtime API。

## 请求路径

```text
Browser
  │ selected MAGI + cookie
  ▼
magi-webui
  │ discovers running MAGI, then routes only to the selected target
  │ signs method + path + identity capabilities + magic_id
  ▼
magi Runtime Service
  │ checks MAGI_RUNTIME_ID and HMAC freshness
  ▼
private SQLite, workspace, MAGIS database
```

WebUI 代理路径为：

```text
/api/runtime/<magic_id>/<runtime-api-path>
```

浏览器代码不会提供上游地址。WebUI 只从 MAGI 注册和 `eva_runtimes.deployment_name`
推导 Service 名；Genesis 的初始 MAGI 使用 `magi` Service。一个已停止或尚未启动的
MAGI 返回 `runtime.not_running`，而不是尝试访问其私有数据。

## 认证边界

浏览器 Cookie 仅由 `magi-webui` 验证。WebUI 使用 `MAGI_CONTROL_SECRET` 生成 60 秒
有效的 HMAC；签名覆盖 HTTP method、完整 path/query、时间戳、目标 MAGI ID 和操作者 ID。
每个 runtime 必须有 `MAGI_RUNTIME_ID`，并拒绝目标 ID 不匹配或签名过期的请求。

被验证的身份会映射到目标 MAGI 私有 SQLite 的 Contact，以保持原有的会话、任务和联系人
ID 作用域。映射使用 Telegram ID；MAGI 私有 SQLite 主键不会跨节点传播。

权限和验证数据归属如下：

- `magis_admins` 位于某个 MAGIS 的 PostgreSQL：一个 MAGIS 可有多个 Admin；授权不继承到
  父 MAGIS 或子 MAGIS。
- `contacts.role='assigned'` 位于目标 MAGI 本地 SQLite：一个 MAGI 最多一个 assigned user。
- 登录验证码位于**被登录 MAGI**的本地 SQLite。该 MAGI 有自己的 Bot 时由自己投递；尚未
  配置 Bot 时，仅可由其**直接所属 MAGIS**的 ADAM Bot 代发。ADAM 只投递，不保存验证码，
  也不获得子节点登录权限。
- Bot Token 始终只写入目标 MAGI 的本地 SQLite。WebUI 不持有 Token。

## 前端目标选择与缓存

登录页先显示正在运行的 MAGI，再显示所选 MAGI 可登录的账号（其直接 MAGIS 的 Admin 与
该节点的 assigned user）。成功登录后 cookie 固定 `selected_magic_id`；代理拒绝任何不同
目标的请求。切换 MAGI 必须回到登录页重新认证，而不是在已登录页面的顶部切换。

MAGIS/MAGI 管理 API 也在目标 runtime 中执行：Admin 只能管理该 runtime 的直接 MAGIS
及其直接 MAGI。停止、重启或删除当前登录的 MAGI 会被拒绝，避免浏览器会话被主动切断。

## Kubernetes

- `deploy/k8s/control/webui-deployment.yaml`：生产 `magi-webui` Deployment；命令为
  `magi webui`，只使用运行时注册元数据和内部服务，不挂载 PVC 或 workspace。
- `deploy/k8s/base/deployment.yaml`：初始 MAGI runtime；不再承载浏览器 SPA。
  `MAGI_WORKSPACE_DIR=/workspace` 指向 PVC 挂载点。
- orchestrator 在启动新的 MAGI 时，同时创建同名的内部 ClusterIP Service；停止时保留，
  删除 MAGI 时一并删除。
- `deploy/k8s-dev/control-dev/`：kind 开发 overlay。仍使用 `magi:dev` 这个同一镜像标签，
  但用 Vite HMR 服务统一 WebUI，后端监听容器内 `:8000`。

生产启动使用 `deploy/k8s/bootstrap-k8s.sh`；脚本会部署 orchestrator、初始 runtime 和
`magi-webui`，并提示将本地端口转发到 `svc/magi-webui`。dev 模式请改用
`deploy/k8s-dev/bootstrap-k8s-dev.sh`。非容器单机部署走 `deploy/cli/`，每个 MAGI
是独立 OS 进程（`magi local start` 或 systemd 管理）。

## 仍需后续增强的部分

- 网络策略：限制 Runtime Service 只接受 `magi-webui` Namespace/Pod 的流量。
- mTLS 或服务身份：替代当前共享 HMAC 密钥。
- 可用性与流式代理：对停止的 MAGI 显示更丰富的状态，并为聊天支持 SSE。
- Bot Token 的加密与密钥轮换：Token 当前只位于各 MAGI 私有数据库；生产环境应使用外部
  密钥管理服务。
