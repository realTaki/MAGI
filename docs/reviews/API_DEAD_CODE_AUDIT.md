# Channels API ↔ WebUI 前端 死代码审计

日期：2026-08-16
状态：发现 20 处死代码/不可达路由，分四类列出。

## 1. 审计目标

对比 [`magi/channels/api/`][api-dir] 下注册的所有 FastAPI 端点与 [`app/src/`][webui-src]
前端代码中实际发出的请求，统计：

1. 路由已注册但前端从未调用（pure dead code）。
2. 路由已注册但前端走的是另一条等价路径（被替代路由）。
3. 路由已注册、但因前端 `runtimeUrl()` 重写规则实际不可达（mount 死代码）。
4. 路由仅供运维探针使用，前端不会触发（操作型死代码，但应保留）。

不统计的对象：

- Telegram 通道内部 worker（走 python-telegram-bot 长连接，不发 HTTP）。
- LLM Provider、Tool、MCP server 等内部组件（也不发 HTTP）。
- 测试与 CI 脚本里出现的 URL（仅作旁证）。

## 2. 拓扑背景（影响可达性）

单例 WebUI 与每个 MAGI runtime 是两个独立进程。前端发出的 `/api/...`
请求最终落在三处之一：

| 落点 | 路径前缀 | 路由来源 |
| --- | --- | --- |
| 控制面 WebUI（自身） | `/api/auth`、`/api/magi`、`/api/magis`、`/api/control`、`/api/runtime` | 单例进程直挂 |
| 运行时容器 | `/api/{其他}` 经 `runtime_proxy.api_route("/{path:path}")` 转发 | HMAC 签名后转发 |

关键点：前端 `lib/queryClient.ts:51-58` 的 `isControlPath()` 决定是否
走 `runtime_proxy` 重写：

```ts
function isControlPath(url: string): boolean {
  if (url === "/api/magi" || /^\/api\/magi\/\d+(?:\/|$)/.test(url)) {
    return true;
  }
  return ["/api/auth", "/api/runtime", "/api/magis"].some(
    (prefix) => url === prefix || url.startsWith(`${prefix}/`) || url.startsWith(`${prefix}?`),
  );
}
```

非控制路径一律被改写为 `/api/runtime/{selectedMagiId}{原路径}`，再由
控制面 `runtime_proxy.api_route("/runtime/{magi_id}/{path:path}")`
[签名转发][runtime-proxy] 到对应 MAGI runtime 的内部 API。

→ 这意味着：mount 在 **控制面** 的 `runtime_provider` 路由
（`/api/magi/self/provider` GET/PATCH/DELETE）虽然存在，但前端永远走
proxy 路径，控制面这层挂载对前端不可达。runtime 那一侧的同名路由是
可达的——前端真正用的是 `/api/runtime/{magi_id}/magi/self/provider`。
详见 [§5 C 类][c-class]。

## 3. 路由全集（注册侧）

下表覆盖 [`magi/channels/api/`][api-dir] 下全部 `@router.{get,post,put,patch,delete,api_route}`
声明；mount 实际前缀来自 [`magi/channels/api/app.py`][app-py]。

| 方法 | 路径 | 文件 | 行 | 挂载位置 |
| --- | --- | --- | --- | --- |
| GET | `/health` | app.py | 189 | 控制面 root |
| GET | `/health/channels` | health.py | 10 | 控制面 root |
| GET | `/health/workers` | health.py | 19 | 控制面 root |
| GET | `/api/auth/available-magi` | auth.py | 237 | 控制面 |
| GET | `/api/auth/targets/{magi_id}/accounts` | auth.py | 252 | 控制面 |
| POST | `/api/auth/targets/{magi_id}/send-login-code` | auth.py | 258 | 控制面 |
| POST | `/api/auth/targets/{magi_id}/verify-login-code` | auth.py | 302 | 控制面 |
| POST | `/api/auth/targets/{magi_id}/local-direct-login` | auth.py | 315 | 控制面 |
| POST | `/api/auth/logout` | auth.py | 337 | 控制面 |
| GET | `/api/auth/me` | auth.py | 343 | 控制面 |
| GET | `/api/magi` | magi.py | 259 | 控制面 |
| POST | `/api/magi` | magi.py | 272 | 控制面 |
| GET | `/api/magi/{magi_id}` | magi.py | 314 | 控制面 |
| PATCH | `/api/magi/{magi_id}` | magi.py | 320 | 控制面 |
| POST | `/api/magi/{magi_id}/runtime/start` | magi.py | 359 | 控制面 |
| POST | `/api/magi/{magi_id}/runtime/stop` | magi.py | 364 | 控制面 |
| DELETE | `/api/magi/{magi_id}` | magi.py | 369 | 控制面 |
| GET | `/api/magi/self/instruction` | magi.py (`self_router`) | 390 | runtime |
| PUT | `/api/magi/self/instruction` | magi.py (`self_router`) | 399 | runtime |
| GET | `/api/magi/self/provider` | runtime_provider.py | 144 | **控制面 + runtime** |
| PATCH | `/api/magi/self/provider` | runtime_provider.py | 150 | **控制面 + runtime** |
| DELETE | `/api/magi/self/provider` | runtime_provider.py | 175 | **控制面 + runtime** |
| GET | `/api/magis` | magis.py | 290 | 控制面 + runtime |
| POST | `/api/magis` | magis.py | 305 | 控制面 + runtime |
| GET | `/api/magis/{magis_id}` | magis.py | 342 | 控制面 + runtime |
| PATCH | `/api/magis/{magis_id}` | magis.py | 348 | 控制面 + runtime |
| DELETE | `/api/magis/{magis_id}` | magis.py | 372 | 控制面 + runtime |
| GET | `/api/magis/{magis_id}/roles` | magis.py | 383 | 控制面 + runtime |
| POST | `/api/magis/{magis_id}/roles` | magis.py | 390 | 控制面 + runtime |
| PATCH | `/api/magis/{magis_id}/roles/{role_id}` | magis.py | 410 | 控制面 + runtime |
| DELETE | `/api/magis/{magis_id}/roles/{role_id}` | magis.py | 445 | 控制面 + runtime |
| GET | `/api/magis/{magis_id}/memberships` | magis.py | 465 | 控制面 + runtime |
| POST | `/api/magis/{magis_id}/memberships` | magis.py | 474 | 控制面 + runtime |
| PATCH | `/api/magis/{magis_id}/memberships/{membership_id}` | magis.py | 499 | 控制面 + runtime |
| DELETE | `/api/magis/{magis_id}/memberships/{membership_id}` | magis.py | 531 | 控制面 + runtime |
| GET | `/api/magis/{magis_id}/admins` | magis.py | 557 | 控制面 + runtime |
| POST | `/api/magis/{magis_id}/admins` | magis.py | 564 | 控制面 + runtime |
| DELETE | `/api/magis/{magis_id}/admins/{admin_id}` | magis.py | 603 | 控制面 + runtime |
| POST | `/api/control/telegram/bootstrap` | runtime_control.py | 40 | 控制面 |
| POST | `/api/control/telegram/verify` | runtime_control.py | 57 | 控制面 |
| POST | `/api/control/telegram/send` | runtime_control.py | 69 | 控制面 |
| GET | `/api/access/login-accounts` | runtime_access.py | 250 | runtime |
| POST | `/api/access/send-login-code` | runtime_access.py | 265 | runtime |
| POST | `/api/access/local-direct-login` | runtime_access.py | 322 | runtime |
| POST | `/api/access/verify-login-code` | runtime_access.py | 350 | runtime |
| POST | `/api/access/two-factor/send-login-code` | runtime_access.py | 385 | runtime |
| POST | `/api/access/two-factor/verify-login-code` | runtime_access.py | 419 | runtime |
| GET | `/api/contacts` | contacts.py | 131 | runtime |
| POST | `/api/contacts` | contacts.py | 199 | runtime |
| GET | `/api/contacts/{contact_id}/notes` | contacts.py | 316 | runtime |
| GET | `/api/contacts/{contact_id}` | contacts.py | 334 | runtime |
| PATCH | `/api/contacts/{contact_id}` | contacts.py | 350 | runtime |
| GET | `/api/contacts/{contact_id}/token-usage` | token_metrics.py | 172 | runtime |
| GET | `/api/telegram/bind` | tg_bindings.py | 42 | runtime |
| POST | `/api/telegram/bind` | tg_bindings.py | 42 | runtime |
| DELETE | `/api/telegram/bind/{tgid}` | tg_bindings.py | 84 | runtime |
| GET | `/api/telegram/bind/{tgid}` | tg_bindings.py | 129 | runtime |
| POST | `/api/chat/send` | chat.py | 123 | runtime |
| GET | `/api/chat/notifications/{job_id}` | chat.py | 314 | runtime |
| POST | `/api/chat/conversations` | chat_conversations.py | 269 | runtime |
| GET | `/api/chat/conversations` | chat_conversations.py | 309 | runtime |
| GET | `/api/chat/conversations/{conversation_id}` | chat_conversations.py | 353 | runtime |
| DELETE | `/api/chat/conversations/{conversation_id}` | chat_conversations.py | 378 | runtime |
| PATCH | `/api/chat/conversations/{conversation_id}` | chat_conversations.py | 400 | runtime |
| GET | `/api/chat/conversations/{conversation_id}/messages` | chat_conversations.py | 498 | runtime |
| GET | `/api/chat/search` | chat_search.py | 43 | runtime |
| GET | `/api/action_items` | action_items.py | 148 | runtime |
| POST | `/api/action_items/{item_id}/complete` | action_items.py | 186 | runtime |
| GET | `/api/memory` | memory.py | 114 | runtime |
| GET | `/api/soul` | soul.py | 79 | runtime |
| PUT | `/api/soul` | soul.py | 97 | runtime |
| POST | `/api/soul/reset` | soul.py | 128 | runtime |
| GET | `/api/tg-settings/read-reaction` | tg_settings.py | 103 | runtime |
| PUT | `/api/tg-settings/read-reaction` | tg_settings.py | 112 | runtime |
| GET | `/api/tg-settings/done-reaction` | tg_settings.py | 144 | runtime |
| PUT | `/api/tg-settings/done-reaction` | tg_settings.py | 154 | runtime |
| GET | `/api/channels` | channels.py | 94 | runtime |
| POST | `/api/channels` | channels.py | 118 | runtime |
| GET | `/api/system-settings/timezone` | system_settings.py | 89 | runtime |
| PUT | `/api/system-settings/timezone` | system_settings.py | 105 | runtime |
| GET | `/api/system-settings/tool-max-iterations` | system_settings.py | 161 | runtime |
| PUT | `/api/system-settings/tool-max-iterations` | system_settings.py | 180 | runtime |
| GET | `/api/system-settings/compact-config` | system_settings.py | 221 | runtime |
| PUT | `/api/system-settings/compact-config` | system_settings.py | 251 | runtime |
| GET | `/api/tasks` | tasks.py | 138 | runtime |
| GET | `/api/tasks/{task_id}` | tasks.py | 143 | runtime |
| POST | `/api/tasks` | tasks.py | 151 | runtime |
| PATCH | `/api/tasks/{task_id}` | tasks.py | 194 | runtime |
| DELETE | `/api/tasks/{task_id}` | tasks.py | 214 | runtime |
| POST | `/api/tasks/{task_id}/run` | tasks.py | 221 | runtime |
| GET | `/api/tasks/{task_id}/runs` | tasks.py | 238 | runtime |
| GET | `/api/tools` | tools.py | 119 | runtime |
| GET | `/api/mcp-servers` | mcp_servers.py | 207 | runtime |
| GET | `/api/mcp-servers/{name}` | mcp_servers.py | 222 | runtime |
| GET | `/api/mcp-servers/{name}/tools` | mcp_servers.py | 296 | runtime |
| POST | `/api/mcp-servers` | mcp_servers.py | 338 | runtime |
| PATCH | `/api/mcp-servers/{name}` | mcp_servers.py | 383 | runtime |
| DELETE | `/api/mcp-servers/{name}` | mcp_servers.py | 443 | runtime |
| POST | `/api/mcp-servers/{name}/toggle` | mcp_servers.py | 467 | runtime |
| GET | `/api/skills` | skills.py | 94 | runtime |
| PATCH | `/api/skills/{name}` | skills.py | 113 | runtime |
| GET | `/api/skills/{name}/raw` | skills.py | 145 | runtime |
| POST | `/api/runtime/{magi_id}/{path:path}` | runtime_proxy.py | 38 | 控制面 |
| ALL | `/api/{path:path}` | runtime_proxy.py | 146 | 控制面 |

合计 **97 条**。

## 4. 前端调用全集

通过 [`lib/queries.ts`][queries-ts]（中心 React Query 层）+ 散落在 pages/components
的 raw `fetch()` 聚合。`runtimeUrl()` 重写后，前端发出的实际路径前缀已在上表
"挂载位置" 列体现——控制面挂载的路由被原样发往控制面，其他被改写为
`/api/runtime/{id}/...` 后由控制面代理。

### 4.1 通过 `lib/queries.ts` 的 hook（中心化层）

来源：[`app/src/lib/queries.ts`][queries-ts]。

| 方法 | 路径 | Hook |
| --- | --- | --- |
| GET | `/api/contacts` | `useContacts`, `useAdminContacts` |
| GET | `/api/magis` | `useMagis` |
| GET | `/api/magi` | `useMagi` |
| GET | `/api/tasks` | `useTasks` |
| GET | `/api/tasks/{task_id}/runs` | `useTaskRuns` |
| GET | `/api/action_items` | `useActionItems` |
| GET | `/api/skills` | `useSkills` |
| GET | `/api/system-settings/timezone` | `useSystemTimezone` |
| GET | `/api/mcp-servers` | `useMcpServers` |
| GET | `/api/mcp-servers/{name}/tools` | `useMcpServerTools` |
| POST | `/api/mcp-servers` | `useCreateMcpServer` |
| PATCH | `/api/mcp-servers/{name}` | `useUpdateMcpServer` |
| DELETE | `/api/mcp-servers/{name}` | `useDeleteMcpServer` |
| POST | `/api/mcp-servers/{name}/toggle` | `useToggleMcpServer` |
| GET | `/api/auth/me` | `useMe` |
| GET | `/api/auth/available-magi` | `useAvailableMagi` |
| GET | `/api/auth/targets/{magi_id}/accounts` | `useTargetLoginAccounts` |
| POST | `/api/auth/targets/{magi_id}/send-login-code` | `useSendTargetLoginCode` |
| POST | `/api/auth/targets/{magi_id}/verify-login-code` | `useVerifyTargetLoginCode` |
| POST | `/api/auth/targets/{magi_id}/local-direct-login` | `useLocalDirectLogin` |
| POST | `/api/auth/logout` | `useLogout` |
| POST | `/api/control/telegram/verify` | `useVerifyBot` |
| POST | `/api/control/telegram/bootstrap` | `useSaveBot` |
| GET | `/api/system-settings/tool-max-iterations` | `useToolMaxIterations` |
| PUT | `/api/system-settings/tool-max-iterations` | `useUpdateToolMaxIterations` |
| GET | `/api/system-settings/compact-config` | `useCompactConfig` |
| PUT | `/api/system-settings/compact-config` | `useUpdateCompactConfig` |
| GET | `/api/tg-settings/{read,done}-reaction` | `useTgReaction` |
| PUT | `/api/tg-settings/{read,done}-reaction` | `useUpdateTgReaction` |
| GET | `/api/soul` | `useSoul` |
| PUT | `/api/soul` | `useUpdateSoul` |
| POST | `/api/soul/reset` | `useResetSoul` |
| GET | `/api/chat/search` | `useChatSearch` |
| GET | `/api/chat/conversations` | `useChatConversations` |
| GET | `/api/chat/conversations/{id}` | `useChatConversation` |
| GET | `/api/tasks/{task_id}` | `useTask` |
| POST | `/api/tasks` | `useCreateTask` |
| PATCH | `/api/tasks/{task_id}` | `useUpdateTask` |
| DELETE | `/api/tasks/{task_id}` | `useDeleteTask` |
| POST | `/api/tasks/{task_id}/run` | `useRunTaskNow` |

### 4.2 散落的 raw `fetch()`（绕过 hooks 层）

| 方法 | 路径 | 调用点 |
| --- | --- | --- |
| GET | `/api/system-settings/timezone` | [`SettingsSystemTimezoneCard.tsx:62`][tz-card] |
| GET | `/api/chat/conversations/{id}/messages` | [`ChatTab.tsx:270`][chat-tab-270], [`ChatTab.tsx:363`][chat-tab-363] |
| GET | `/api/chat/conversations?limit=&offset=` | [`ChatTab.tsx:397`][chat-tab-397] |
| GET | `/api/chat/conversations/{id}` | [`ChatTab.tsx:431`][chat-tab-431] |
| PATCH | `/api/chat/conversations/{id}` | [`ChatTab.tsx:530`][chat-tab-530] |
| DELETE | `/api/chat/conversations/{id}` | [`ChatTab.tsx:571`][chat-tab-571] |
| POST | `/api/chat/send` | [`ChatTab.tsx:608`][chat-tab-608] |
| POST | `/api/magis/{id}` (PATCH) | [`MagisPane.tsx:94`][magis-pane-94] |
| POST | `/api/magis` (POST) | [`MagisPane.tsx:107`][magis-pane-107] |
| DELETE | `/api/magis/{id}` | [`MagisPane.tsx:118`][magis-pane-118] |
| POST | `/api/magi` | [`MagicPane.tsx:78`][magic-pane-78] |
| PATCH | `/api/magi/{id}` | [`MagicPane.tsx:96`][magic-pane-96] |
| POST | `/api/magi/{id}/runtime/{start\|stop}` | [`MagicPane.tsx:109`][magic-pane-109] |
| POST | `/api/runtime/{magi_id}/magi/self/provider` | [`MagicPane.tsx:122`][magic-pane-122] |
| DELETE | `/api/magi/{id}` | [`MagicPane.tsx:283`][magic-pane-283] |
| PATCH | `/api/skills/{name}` | [`KnowledgeSkillsPane.tsx:24`][skills-pane-24] |
| POST | `/api/action_items/{id}/complete` | [`ActionItemsPane.tsx:155`][action-pane-155] |
| POST | `/api/tasks/{task_id}/run` | [`TaskListPane.tsx:214`][task-pane-214] |
| PATCH | `/api/tasks/{task_id}` | [`TaskListPane.tsx:242`][task-pane-242] |
| POST | `/api/channels` | [`SettingsChannelsCard.tsx:86`][channels-card-86] |
| POST | `/api/control/telegram/verify` | [`BotTokenField.tsx:56`][bot-56] |
| POST | `/api/control/telegram/bootstrap` | [`BotTokenField.tsx:80`][bot-80] |
| POST | `/api/access/two-factor/send-login-code` | [`SettingsSecurityCard.tsx:22`][sec-22] |
| POST | `/api/access/two-factor/verify-login-code` | [`SettingsSecurityCard.tsx:35`][sec-35] |
| GET | `/api/magi/self/instruction` | [`SettingsInstructionCard.tsx:7`][ins-card-7] |
| PUT | `/api/magi/self/instruction` | [`SettingsInstructionCard.tsx:8`][ins-card-8] |
| GET | `/api/magis/{id}/roles` | [`SocietyControls.tsx:12`][society-12] |
| GET | `/api/magis/{id}/memberships` | [`SocietyControls.tsx:12`][society-12] |
| POST | `/api/magis/{id}/roles` | [`SocietyControls.tsx:15`][society-15] |
| DELETE | `/api/magis/{id}/roles/{role_id}` | [`SocietyControls.tsx:16`][society-16] |
| DELETE | `/api/magis/{id}/memberships/{id}` | [`SocietyControls.tsx:16`][society-16] |
| GET | `/api/contacts/{uid}/token-usage` | [`KnowledgeContactsPane.tsx:34`][contacts-34] |
| GET | `/api/contacts/{detailId}/notes` | [`KnowledgeContactsPane.tsx:86`][contacts-86] |
| GET | `/api/mcp-servers/{name}/tools` | [`KnowledgeMCPPane.tsx:119`][mcp-119] |

注：`KnowledgeMCPPane.tsx:119` 与 `lib/queries.ts` 内的同名 hook
是同一调用点的双实现——见 [§6 注解][mcp-dup]。

## 5. 死代码清单

按"前端永远到不了 / 永远不调用"的程度分为四类。

### A 类：路由存在但前端从未调用（pure dead code）— 14 处

| # | 方法 | 路径 | 备注 |
| --- | --- | --- | --- |
| A1 | GET | `/api/magi/{magi_id}` | [magi.py:314][magi-py] — 前端只 LIST/POST/PATCH/runtime-start-stop/DELETE，从来不读单个 |
| A2 | GET | `/api/magis/{magis_id}/admins` | [magis.py:557][magis-py] — Magi `admins` 子资源完全未触；UI 中 `magis_admins` 只在 i18n 字符串/评论里出现 |
| A3 | POST | `/api/magis/{magis_id}/admins` | [magis.py:564][magis-py] — 同上 |
| A4 | DELETE | `/api/magis/{magis_id}/admins/{admin_id}` | [magis.py:603][magis-py] — 同上 |
| A5 | PATCH | `/api/magis/{magis_id}/roles/{role_id}` | [magis.py:410][magis-py] — UI 只 GET / POST / DELETE role，PATCH 未实现 |
| A6 | POST | `/api/magis/{magis_id}/memberships` | [magis.py:474][magis-py] — UI 只 GET / DELETE membership，POST 未实现 |
| A7 | PATCH | `/api/magis/{magis_id}/memberships/{id}` | [magis.py:499][magis-py] — 同上 |
| A8 | POST | `/api/contacts` | [contacts.py:199][contacts-py] — UI 只读 contacts，没有"创建 contact" 入口 |
| A9 | GET | `/api/contacts/{contact_id}` | [contacts.py:334][contacts-py] — UI 通过 `/notes`、`/token-usage` 间接取；从来不直读单个 contact |
| A10 | PATCH | `/api/contacts/{contact_id}` | [contacts.py:350][contacts-py] — 同上，UI 没有"编辑 contact 元数据" 入口 |
| A11 | GET | `/api/mcp-servers/{name}` | [mcp-servers.py:222][mcp-py] — UI 列出后 PATCH/DELETE；从来不取单个 |
| A12 | GET | `/api/skills/{name}/raw` | [skills.py:145][skills-py] — UI 不读原始 SKILL.md 文本（只 toggle enabled） |
| A13 | POST | `/api/chat/conversations` | [chat_conversations.py:269][chat-convs-py] — `chat_send` 路由自动 create；前端从未单独 POST，见 [`ChatTab.tsx:446`][chat-tab-446] 的注释（"eagerly POSTed" 描述的是设计意图，不是当前调用） |
| A14 | POST | `/api/control/telegram/send` | [runtime_control.py:69][rc-py] — "发送一条测试消息到 tgid" 的运维钩子；前端无入口 |

### B 类：被另一条等价路由替代 — 4 处

| # | 方法 | 路径 | 替代为 | 替代调用点 |
| --- | --- | --- | --- | --- |
| B1 | POST | `/api/telegram/bind` | （无前端调用） | UI 用 `/api/control/telegram/{verify,bootstrap}` 完成引导；bind 端点已脱钩 |
| B2 | DELETE | `/api/telegram/bind/{tgid}` | （无前端调用） | 同上 |
| B3 | GET | `/api/telegram/bind/{tgid}` | （无前端调用） | 同上 |
| B4 | GET | `/api/access/login-accounts` | `GET /api/auth/targets/{magi_id}/accounts` | [`queries.ts:388`][queries-ts] — control-plane 用 auth 路由做 picker，runtime_access 端的 login-accounts 没有调用者 |

→ tg_bindings 与 runtime_access 中这 4 处像是"初版向导/admin 表"的遗迹，
引导流程整体迁移到了 `runtime_control.telegram.{verify,bootstrap}` +
`auth.targets.*`。如果业务确认废弃，应删除整组端点并撤掉对应 mount。

### C 类：mount 在控制面但前端不可达（控制面这一侧死代码，runtime 端仍然可达）— 3 处

| # | 方法 | 路径 | 控制面 mount 不可达的原因 |
| --- | --- | --- | --- |
| C1 | GET | `/api/magi/self/provider` | [`runtime_provider.py:144`][rp-py] — 控制面 mount 后，前端通过 `runtimeUrl()` 改写为 `/api/runtime/{id}/magi/self/provider`；`isControlPath` 不匹配 `/api/magi/self/...`（只匹配 `/api/magi/{id}` 字面），所以控制面这层永远收不到 |
| C2 | PATCH | `/api/magi/self/provider` | 同上，[`runtime_provider.py:150`][rp-py] |
| C3 | DELETE | `/api/magi/self/provider` | 同上，[`runtime_provider.py:175`][rp-py] |

→ 真正常用的是 [`MagicPane.tsx:122`][magic-pane-122] 的 `/api/runtime/{magi_id}/magi/self/provider`，
落到 runtime 那一侧的同名挂载。要么：

1. 删掉控制面这层的 `runtime_provider` 挂载（`app.py:216`），只让 runtime
   保留它——更符合注释里"控制面不能读取/写入其他 MAGI 的 node-local settings"的意图
   ([`magi.py` 顶部 docstring][magi-py-top])。
2. 或在控制面只允许 admin 写其他 MAGI 的 provider；前端写别的 MAGI 时绕开
   `isControlPath`，但目前没有任何调用源会这么做。

推荐 (1)，见 [§7][rec]。

### D 类：仅运维/探针使用，前端永远不触发 — 2 处

| # | 方法 | 路径 | 保留理由 |
| --- | --- | --- | --- |
| D1 | GET | `/health/channels` | [health.py:10][health-py] — 通道 worker 健康探针 |
| D2 | GET | `/health/workers` | [health.py:19][health-py] — worker 池健康探针 |

→ 这两条是 [deployment probes][deploy-probes]，**应保留**。

## 6. 顺便发现的小问题

### `KnowledgeMCPPane.tsx:119` 与 `lib/queries.ts:228` 重复实现

两边都实现了 `GET /api/mcp-servers/{name}/tools`。前者是 raw `fetch`，
后者是 `useMcpServerTools` React Query hook。两者在同一文件内并存，
旧实现没被删除；建议合并到 hook，或在 [mcp-dup] 处给 raw fetch 加 `// TODO`
注释。

### `/api/auth` 出现在 `queryClient.ts:55` 但从未作为 endpoint 被请求

`isControlPath()` 里把 `/api/auth` 当作前缀匹配——意味着前端若手写一个
`apiFetch("/api/auth")`，会被当作控制面路径直发。但前端没有任何代码以
`/api/auth`（不带子路径）作为目标调用；这条规则当前是"未来扩展位"。
要么删掉，要么写一行注释说明意图。

### `/api/meta/node-role` 和 `/api/auth/allowed-tgids`

二者都仅在 [`DashboardPage.tsx:21`][dashboard-21] / [224][dashboard-224]
的注释里出现，**没有任何 fetch 调用**。意味着后端没有这条路由（确实没有，
见 §3）—— 注释里描述的功能要么是规划中，要么已经废弃。

## 7. 处置建议 [rec]

按风险/收益排列：

1. **C1-C3 立即处理** — `runtime_provider` 在控制面这层无用，按
   [`magi.py` 顶部 docstring][magi-py-top] 的本意，控制面根本不该能
   读写其他 MAGI 的 node-local settings。删 `app.py:216` 这一行即可，
   runtime 端 mount 保留不变，前端行为不变（继续走 proxy）。
2. **A1, A8-A10, A11, A12, A13** — 删除对应路由 + DTO + 单元测试。
   它们都已属于"无 UI 入口"阶段，留着只会让 OpenAPI 文档误导新人。
3. **A2-A4** — `magis/{id}/admins` 整组端点没被任何 UI 消费。建议跟
   `Contact` 在 runtime 里"运营治理" 一起评估：要么补 UI（用于 admin 列表），
   要么整组删。
4. **A5-A7** — `roles/{id}` PATCH 和 `memberships` POST/PATCH。后端模型完整，
   删之前先确认 "team instruction" 编辑会不会未来用到这些 mutation。
5. **A14 + B1-B3 + B4** — `control/telegram/send`、`tg_bindings.*`、
   `access/login-accounts` 是历史流程的遗留。建议开个 follow-up 任务：
   确认运营流程是否真用得到 `control/telegram/send`，再用不着用。
   `tg_bindings.*` 与 `access/login-accounts` 已确认前端不可达，应整组删。
6. **D1, D2** — 保留，作为 K8s 探针。

---

## 附：路由号与文件行映射速查

- 总路由数：**97**
- 前端实际可达的不同 (method, path) 组合：**78**
- 死代码/不可达路由：**20**
  - A 类：14
  - B 类：4
  - C 类：3（控制面这层不可达，runtime 端可达）
  - D 类：0 死（保留）
- 健康探针（不计入 dead）：**2**

[api-dir]: ../../py-magi/magi/channels/api/
[webui-src]: ../../app/src/
[app-py]: ../../py-magi/magi/channels/api/app.py
[queries-ts]: ../../app/src/lib/queries.ts
[runtime-proxy]: ../../py-magi/magi/channels/api/runtime_proxy.py
[magi-py]: ../../py-magi/magi/channels/api/magi.py
[magi-py-top]: ../../py-magi/magi/channels/api/magi.py#L1
[magis-py]: ../../py-magi/magi/channels/api/magis.py
[contacts-py]: ../../py-magi/magi/channels/api/contacts.py
[mcp-py]: ../../py-magi/magi/channels/api/mcp_servers.py
[skills-py]: ../../py-magi/magi/channels/api/skills.py
[chat-convs-py]: ../../py-magi/magi/channels/api/chat_conversations.py
[rc-py]: ../../py-magi/magi/channels/api/runtime_control.py
[rp-py]: ../../py-magi/magi/channels/api/runtime_provider.py
[health-py]: ../../py-magi/magi/channels/api/health.py
[deploy-probes]: ../../deploy/

[tz-card]: ../../app/src/components/settings/SettingsSystemTimezoneCard.tsx#L62
[chat-tab-270]: ../../app/src/pages/ChatTab.tsx#L270
[chat-tab-363]: ../../app/src/pages/ChatTab.tsx#L363
[chat-tab-397]: ../../app/src/pages/ChatTab.tsx#L397
[chat-tab-431]: ../../app/src/pages/ChatTab.tsx#L431
[chat-tab-530]: ../../app/src/pages/ChatTab.tsx#L530
[chat-tab-571]: ../../app/src/pages/ChatTab.tsx#L571
[chat-tab-608]: ../../app/src/pages/ChatTab.tsx#L608
[chat-tab-446]: ../../app/src/pages/ChatTab.tsx#L446
[magis-pane-94]: ../../app/src/pages/agentic-society/MagisPane.tsx#L94
[magis-pane-107]: ../../app/src/pages/agentic-society/MagisPane.tsx#L107
[magis-pane-118]: ../../app/src/pages/agentic-society/MagisPane.tsx#L118
[magic-pane-78]: ../../app/src/pages/agentic-society/MagicPane.tsx#L78
[magic-pane-96]: ../../app/src/pages/agentic-society/MagicPane.tsx#L96
[magic-pane-109]: ../../app/src/pages/agentic-society/MagicPane.tsx#L109
[magic-pane-122]: ../../app/src/pages/agentic-society/MagicPane.tsx#L122
[magic-pane-283]: ../../app/src/pages/agentic-society/MagicPane.tsx#L283
[skills-pane-24]: ../../app/src/pages/knowledge/KnowledgeSkillsPane.tsx#L24
[action-pane-155]: ../../app/src/pages/chat/ActionItemsPane.tsx#L155
[task-pane-214]: ../../app/src/pages/chat/TaskListPane.tsx#L214
[task-pane-242]: ../../app/src/pages/chat/TaskListPane.tsx#L242
[channels-card-86]: ../../app/src/components/settings/SettingsChannelsCard.tsx#L86
[bot-56]: ../../app/src/components/settings/BotTokenField.tsx#L56
[bot-80]: ../../app/src/components/settings/BotTokenField.tsx#L80
[sec-22]: ../../app/src/components/settings/SettingsSecurityCard.tsx#L22
[sec-35]: ../../app/src/components/settings/SettingsSecurityCard.tsx#L35
[ins-card-7]: ../../app/src/components/settings/SettingsInstructionCard.tsx#L7
[ins-card-8]: ../../app/src/components/settings/SettingsInstructionCard.tsx#L8
[society-12]: ../../app/src/pages/agentic-society/SocietyControls.tsx#L12
[society-15]: ../../app/src/pages/agentic-society/SocietyControls.tsx#L15
[society-16]: ../../app/src/pages/agentic-society/SocietyControls.tsx#L16
[contacts-34]: ../../app/src/pages/knowledge/KnowledgeContactsPane.tsx#L34
[contacts-86]: ../../app/src/pages/knowledge/KnowledgeContactsPane.tsx#L86
[mcp-119]: ../../app/src/pages/knowledge/KnowledgeMCPPane.tsx#L119
[mcp-dup]: #mcp_knowledgepane_tsx119-与-lib_queries_ts228-重复实现
[dashboard-21]: ../../app/src/pages/DashboardPage.tsx#L21
[dashboard-224]: ../../app/src/pages/DashboardPage.tsx#L224
[rec]: #7-处置建议
[c-class]: #c-class-mount-在控制面但前端不可达控制面这一侧死代码runtime-端仍然可达-3-处
