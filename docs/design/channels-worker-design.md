# Channels 迁移设计书

> 日期：2026-08-08
> 状态：全量切换合同

## 1. 目标与非目标

Channels 将全部切到注入式 `NewBus`。每个通道负责自己的入站适配和出站投递；
Agent 只消费 `ChatJob`、产出 `DeliveryJob`，不调用 Telegram、HTTP 或 WebUI
持久化实现。

这是一次完整切换，不是兼容层：

- 不读取、双写或回退到 `magi.bus`、`get_bus()`、旧 Bus 表、旧 worker 或旧
  HTTP 运行时协议。
- 不让 NewBus 和旧 Bus 同时 claim 同一业务方向；切换完成后删除旧 delivery、
  scheduler、dispatcher 与模块级旧 Bus 入口。
- 不新增 Channel 专属的进程内队列、隐式全局 worker registry 或第二套 agent
  turn 状态机。
- 当前 NewBus board 的物理表与 migration 只属于 `magi.new_bus`。最终独立
  schema 的物理命名由 NewBus migration 一次确定，不是对旧表/字段的兼容承诺。

本文不规定 Agent 的 LLM/tool loop；该合同见
`docs/design/agent-worker-new-bus.md`。Channel 只需遵守其中的 job-board DTO
和 delivery 边界。

## 2. 组件与职责

| 组件 | 入站职责 | 出站职责 | 依赖 |
| --- | --- | --- | --- |
| `TelegramWorker` | Telegram 长轮询、鉴权、session/message 写入、发布 `ChatJob` | 投递 `DeliveryJob(channel="tg")` | `NewBus` + Telegram API |
| `TaskWorker` | 轮询 cron/run-at、消费 `RunTaskJob`、发布 `ChatJob` | 无；回复由目标 channel 投递 | `NewBus` |
| WebUI FastAPI 路由 | 鉴权、session/message 写入、发布 `ChatJob`、立即返回 202 | 无 | `NewBus` |
| `WebUIWorker` | 无 | 消费 `DeliveryJob(channel="webui")`，追加 assistant message | `NewBus` |
| A2A FastAPI 路由 | 验签、接收 request/result、发布或完成 A2A job | 无 | `NewBus` |
| `A2AWorker` | 无 | 消费 `SendA2AJob`，执行对端 HTTP，提交 `SendA2AResult` | `NewBus` + A2A transport |

`ChannelWorker` 是 Telegram、Task、WebUI delivery 的共同生命周期基类：构造
注入 `NewBus`，提供幂等 `start/stop`、health 和 delivery claim 模板。A2A 不
消费 `DeliveryJob`，而是使用专用的 `a2a_job_board`。

## 3. 数据合同

### 3.1 Channel 到 Agent

所有入站请求都发布同一个 `ChatJob`：

```python
ChatJob(
    event_id=source_idempotency_key,
    run_id=run_id,
    conversation_id=conversation_id,
    correlation_id=correlation_id,
    kind="chat" | "task.triggered" | "a2a.request" | "run.cancel",
    payload={
        "text": text,
        "channel": reply_channel,        # "tg" | "webui" | "a2a"
        "uid": uid,
        "session_id": session_id,
        "caller_role": caller_role,
        # 可选：source_channel、task_id、a2a_event_id、reply_to 等来源上下文
    },
)
```

`event_id` 必须来自上游稳定事件：Telegram 使用 chat/message ID，WebUI 使用
请求生成并持久化的 idempotency key，A2A 使用 sender/event ID，Task 使用
`task_id + scheduled window`。重投同一事件必须得到同一处理语义。

`payload["channel"]` 是回复路由，必须是一个真实的 egress channel。Task 是
来源，不是 reply channel；任务应写 `source_channel="task"`，并以 task 的
目标 `tg` 或 `webui` 填入 `channel`。禁止让 Agent 为 `channel="task"` 发布
没有消费者的 delivery job。

取消使用 `ChatJob.kind == "run.cancel"` 和相同的 `conversation_id`。不使用
额外的 cancel board 或 metadata 兼容字段。

### 3.2 Agent 到 Channel

正常回复和失败提示均由 Agent 发布：

```python
DeliveryJob(
    channel="tg" | "webui",
    payload={"text": reply, "session_id": session_id, "uid": uid},
    destination=channel_address_or_none,
    run_id=run_id,
)
```

`ChatJobResult` 只表示输入 turn 的处理结果，不携带回复正文；`DeliveryResult`
只表示外部投递结果。WebUI 的 assistant message 由 `WebUIWorker` 在 delivery
成功路径写入，Telegram 的外部 API 写入由 `TelegramWorker` 完成。

A2A 不是 delivery channel：Agent 的 `message_magi` 生成：

```python
SendA2AJob(
    invocation_id=..., run_id=..., target=..., tool_call_id=...,
    expect_reply=..., request={"text": text, ...},
)
```

`A2AWorker` 提交相同 `invocation_id` 的 `SendA2AResult`；入站 A2A `result`
也完成该 board 上的同一 invocation。A2A request 才转换为 `ChatJob`。

### 3.3 Queue 语义

所有 job board 都有统一的 `publish -> claim -> submit_result` 生命周期。
lease 过期由 `BaseJobBoard` 回收并按其重试上限处理。Worker 不建立自己的
retry queue 或 turn lease。

delivery claim 必须按 channel 隔离：若当前 board API 暂无原子 channel filter，
认领到其他 channel 的 job 必须立即 `release()`，不能提交成功/失败。最终实现
应将 filter 纳入 board claim，避免多个 channel worker 无谓争抢。

## 4. 各通道流程

### 4.1 Telegram

```text
Telegram update
  -> 联系人/角色校验
  -> 建立或获取 session，写 user message
  -> publish ChatJob(channel="tg", destination/会话信息)
  -> AgentWorker
  -> publish DeliveryJob(channel="tg", destination=tg_chat_id)
  -> TelegramWorker 调用 Telegram API
  -> submit DeliveryResult
```

Telegram 的 polling application 必须在 `TelegramWorker.start()` 所在 event loop
初始化和关闭，不再创建守护线程或第二个 asyncio loop。TG 未绑定、非授权用户、
非文本输入等拒绝都在适配层处理，不进入 Agent。

### 4.2 WebUI

```text
POST /api/chat/send
  -> AdminGate + 输入校验
  -> 建立/获取 session，写 user message
  -> publish ChatJob(channel="webui")
  -> 202 {run_id, session_id, status="accepted"}
  -> AgentWorker -> DeliveryJob(channel="webui")
  -> WebUIWorker 写 assistant message
```

HTTP handler 不等待 LLM、工具或 delivery。前端以 `run_id` 查询状态或订阅 SSE；
持久化 session message 是权威结果，SSE 仅是最佳努力的增量。

### 4.3 Task

```text
cron/run_at poll 或 RunTaskJob
  -> 唯一化 scheduled window / task run
  -> 写任务上下文 user message
  -> publish ChatJob(source_channel="task", channel=target_channel)
  -> AgentWorker -> target channel DeliveryJob
```

`TaskWorker` 不等待 Agent reply。cron 的触发去重和 `run_at` 的一次性消费必须落
在 NewBus task books；重启时从持久化状态恢复。手动运行、API 和
`schedule_task` tool 一律发布 `RunTaskJob`，复用同一 fire path。

任务在 Agent terminal 后更新对应 task run 的状态；不得把“已发布 ChatJob”当作
任务已经成功执行。

### 4.4 A2A

```text
对端 request -> 验签/拓扑授权 -> ChatJob(channel="a2a") -> AgentWorker
Agent message_magi -> SendA2AJob -> A2AWorker HTTP POST -> SendA2AResult
对端 result -> 验签/关联 invocation_id -> a2a_job_board.submit_result
```

A2A 是 MAGI 间协作，不是 WebUI、资源或管理员授权的替代路径。`expect_reply`
决定 Agent 是否等待关联结果；单向消息不能被伪装成普通 WebUI delivery。

## 5. 启动、健康与关闭

composition root 在一个 `NewBus` 实例创建后，以确定顺序启动：

```text
providers -> tools -> mcp -> agent -> task -> telegram -> webui -> a2a
```

关闭按反序执行。FastAPI 只承担 WebUI/A2A ingress 和生命周期托管；不得在路由
中创建 worker 或修改模块级 worker registry。临时 `get_current_new_bus()` 仅可
作为正在迁移的旧调用定位工具，切换完成前必须删除。

每个 worker 公开只读 health：运行状态、最近 poll/success/error、该 channel
的 pending depth。队列过深只记录节流与告警；不得静默丢弃 job。

## 6. 实施顺序

1. 冻结本文件和 Agent 文档中的 DTO/board 合同，删除版本覆盖段落与不属于
   job-board 模型的状态机设计。
2. 完成 NewBus-only ingress：Telegram、WebUI、Task、A2A 只使用 Books 和
   `ChatJob`/`SendA2AJob`；消除 `get_bus()` fallback。
3. 完成 egress：Telegram/WebUI 使用 `delivery_job_board`；A2A 使用
   `a2a_job_board`；修正 Task 的真实 reply channel 和 task-run terminal
   projection。
4. 在 composition root 注册并启动全部 worker；建立独立 NewBus schema/migration
   与状态目录；删除旧 channels worker、scheduler、dispatcher 和旧 Bus 访问。
5. 上述代码全部落地后才运行完整测试、迁移验证和端到端 smoke。实施期间只做
   静态 import/编译/文档检查，不逐个功能运行 pytest。

## 7. 最终验收

- `magi/channels/`、Agent 和启动路径没有 `magi.bus` / `get_bus()` 生产依赖；
- TG、WebUI、Task、A2A 入站各自生成可去重的正确 board job；
- 回复只进入有消费者的 `tg`/`webui` delivery，A2A 只走 A2A board；
- 取消、steering、LLM 失败、工具/A2A 超时、delivery 失败都产生正确 terminal
  result；
- cron window 不重复触发，`run_at` 恰好一次，task run 有终态；
- worker 启动/关闭无额外 event loop、无重复消费，lease 过期可回收；
- 全量测试、独立 schema migration 和 TG/WebUI/Task/A2A 端到端 smoke 通过。

## 8. 协作同步

修改 public DTO、board 方法、物理 schema、worker 启动顺序或路由语义前，先在
本节追加一行：日期、责任人、变更的合同、影响范围。未经记录，不得以重构为由
增加兼容层、废弃其他 worker 仍依赖的方法，或新建独立的持久化协调层。

- 2026-08-08 / Codex：将历史 v1/v2/v3 拼接文档收敛为本单一合同；明确
  A2A 专用 board、Task reply channel、NewBus-only cutover 与最后统一测试。
  本次未修改生产代码。
