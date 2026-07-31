# 统一 WebUI 与 MAGI Runtime API

## 目标

用户只部署一个 `magi` 镜像，但在 Kubernetes 中选择两种服务角色：

```text
magi                 → 一个 MAGI runtime（默认命令）
magi webui           → 全局唯一的 WebUI 控制面
```

`magi-webui` 是浏览器唯一入口。它承载 React SPA、登录会话、MAGIS/MAGI 管理和
生命周期操作。每个 MAGI runtime 不再挂载 SPA，也不直接暴露给浏览器；它只提供
ClusterIP 可达的 Runtime API。

## 请求路径

```text
Browser
  │ cookie
  ▼
magi-webui
  │ resolves magic_id from the control registry
  │ signs method + path + operator + magic_id
  ▼
magi Runtime Service
  │ checks MAGI_RUNTIME_ID and HMAC freshness
  ▼
private SQLite, /workspace, /magis and direct MAGIS PostgreSQL
```

WebUI 代理路径为：

```text
/api/runtime/<magic_id>/<runtime-api-path>
```

浏览器代码不会提供上游地址。WebUI 只从 MAGI 注册和 `eve_runtimes.deployment_name`
推导 Service 名；Genesis 的初始 MAGI 使用 `magi` Service。一个已停止或尚未启动的
MAGI 返回 `runtime.not_running`，而不是尝试访问其私有数据。

## 认证边界

浏览器 Cookie 仅由 `magi-webui` 验证。WebUI 使用 `MAGI_CONTROL_SECRET` 生成 60 秒
有效的 HMAC；签名覆盖 HTTP method、完整 path/query、时间戳、目标 MAGI ID 和操作者 ID。
每个 runtime 必须有 `MAGI_RUNTIME_ID`，并拒绝目标 ID 不匹配或签名过期的请求。

被验证的操作者会映射到目标 MAGI 私有 SQLite 的 Contact，以保持原有的会话、任务和
联系人 ID 作用域。该映射优先使用 Telegram ID；无 Telegram 的操作者使用系统标记。
因此 WebUI 的登录身份不会要求所有 MAGI 共享 SQLite 或共享 Contact 主键。

## 前端目标选择与缓存

控制面 API（登录、onboarding、MAGIS 树、MAGI 注册）保留在 `/api/*`。私有 Runtime API
由前端自动改写为上述代理路径。顶部 MAGI 选择器保存当前目标；React Query 的私有 key
包含 `runtime/<magic_id>` 前缀。切换目标时，旧目标缓存会被清除，避免 A 的聊天或设置短暂
显示给 B。

## Kubernetes

- `deploy/k8s/control/webui-deployment.yaml`：生产 `magi-webui` Deployment；命令为
  `magi webui`，使用 Genesis MAGIS PostgreSQL 与自己的控制台工作区 PVC。
- `deploy/k8s/base/deployment.yaml`：初始 MAGI runtime；不再承载浏览器 SPA。
- orchestrator 在启动新的 MAGI 时，同时创建同名的内部 ClusterIP Service；停止时保留，
  删除 MAGI 时一并删除。
- `deploy/k8s/control-dev/`：kind 开发 overlay。仍使用 `magi:dev` 这个同一镜像标签，
  但用 Vite HMR 服务统一 WebUI，后端监听容器内 `:8000`。

生产启动使用 `deploy/bootstrap-k8s.sh`；脚本会部署 orchestrator、初始 runtime 和
`magi-webui`，并提示将本地端口转发到 `svc/magi-webui`。

## 仍需后续增强的部分

- 网络策略：限制 Runtime Service 只接受 `magi-webui` Namespace/Pod 的流量。
- mTLS 或服务身份：替代当前共享 HMAC 密钥。
- 可用性与流式代理：对停止的 MAGI 显示更丰富的状态，并为聊天支持 SSE。
- 集中身份目录：当前控制台登录数据在 WebUI 的私有 SQLite；长期可迁入专用控制面
  PostgreSQL schema。
