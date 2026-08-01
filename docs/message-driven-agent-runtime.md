# 消息驱动的 MAGI Actor Runtime（讨论草案）

> 状态：提案，尚未实现。本文不改变当前 runtime 的行为，也不取代现有
> `handle_message()` / tool loop；它定义下一轮执行架构重构应遵循的边界。

## 背景

当前 Telegram、WebUI 和 scheduled-task channel 都会直接调用
`magi.agent.loop.handle_message()`。该函数在一个连续调用栈中执行 LLM →
tool → LLM 的多轮循环。这样在单个交互入口中很直接，但它使 channel 同时承担
入站协议适配和 agent 执行调度两种职责。

当 A2A 进入运行时，如果仍采用同步调用栈，容易变成：

```text
Agent A loop → await Agent B → await tool
```

这会让长时工具、远程 MAGI、重试和用户断线耦合在同一个请求与调用栈中，也无法
统一规定同一 MAGI 的并发、顺序、恢复和幂等。

目标不是引入一个外部控制器来替 MAGI 编排合作，而是让每个 MAGI 成为拥有持久化
mailbox 和可恢复状态的 Actor：channel、tool、proactive policy 和其他 MAGI 都只是
消息生产者或消费者。

## 核心原则

1. **一个 MAGI 是一个 Actor。** 默认以 `magic_id` 为串行化键：同一个 MAGI 一次
   只原子消费一条消息；不同 MAGI 可以并行。
2. **Agent 不知道消息来源。** Telegram、WebUI、task 和 A2A 请求都被标准化为
   相同的 agent inbox message。
3. **外部等待不占用 agent loop。** tool、A2A 和出站投递都作为异步 effect；它们的
   完成、失败或超时会以新消息写回目标 mailbox。
4. **可靠性优先于同步栈。** 每次消费原子提交状态变化和后续消息；进程崩溃、重复
   投递和网络超时是正常情况，不是例外路径。
5. **协调权留给 MAGI。** runtime 只保证可靠投递、串行化、恢复和资源限制；它不
   决定哪个 MAGI 应先行动、何时汇总，或如何处理社会内部的业务协作。

这里的“原子”不是把 LLM、网络和工具调用放在一个数据库事务内。它的含义是：

```text
消费一条 inbox message
  → 读取必要状态
  → 产生状态变化与 effect
  → 在一次本地事务中标记已消费并写入 outbox
```

真正的外部副作用只在事务提交后由 worker 执行。

## 目标架构

```text
Telegram / WebUI / Tasks / Proactive / A2A ingress
                         │
                         ▼
              MAGI private durable inbox
                         │
                         ▼
       Agent Actor (one message, one transition)
          ├── update run / continuation state
          └── write effect(s) to transactional outbox
                         │
         ┌───────────────┼────────────────┐
         ▼               ▼                ▼
     LLM worker      Tool worker    Delivery / A2A worker
         │               │                │
         └──── result / event ────────────┘
                         │
                         ▼
              target MAGI private inbox
```

`magi/channels/` 的职责收敛为协议适配：接收外部输入、发布标准消息，以及消费
出站投递 effect。现有 `channels/dispatcher.py` 的职责更接近出站 delivery router；
长期可考虑改名为 `delivery` 或 `outbound`，但这不是第一阶段的前置条件。

建议新增的运行时边界暂定为 `magi/execution/`：它负责 inbox/outbox、worker lease、
run 生命周期、重试、取消、优先级和资源限制。`magi/agent/` 只处理标准化事件与
agent 状态，不 import Telegram、WebUI、A2A adapter 或具体 delivery 实现。

建议的依赖方向：

```text
channels / tools / proactive → execution contracts
execution workers            → agent.step
agent                         → LLM, memory, tool schemas, execution contracts
```

因此禁止以下依赖：

```text
channels → agent.handle_message
tools    → agent loop
agent    → Telegram / WebUI / A2A 的具体实现
```

## 单 MAGI 部署约束与 SQLite 决策

本设计以当前部署模型为前提：一个 MAGI 对应一个容器/Pod、一个私有 workspace 和
一个 SQLite 文件；其他 MAGI 是远程 runtime，A2A 通过集群内 HTTP 传输。单个 MAGI
内部可以并存 ingress、Agent worker、tool worker、delivery worker 和 task producer，
但它们不共享其他 MAGI 的 SQLite。

第一阶段继续以 SQLite 为唯一持久化事实来源：

```text
SQLite                 durable inbox / jobs / outbox / continuation
asyncio.Event/Condition  本进程低延迟唤醒
短周期 polling           重启恢复与丢失唤醒的兜底
A2A HTTP                 不同 MAGI 之间的传输
```

LLM、网络、工具执行都发生在事务外，因此 SQLite 的单 writer 不应成为当前的主要瓶颈。
相反，用同一个私有 SQLite 原子提交 agent transition 与其后续 jobs/outbox，可以避免
“业务状态已提交、消息却未进入独立 broker”的双写问题。

这不承诺 SQLite 永远足够。只有当同一个 MAGI 需要多 Pod、多机器共享 mailbox，或
SQLite busy/retry 已形成持续瓶颈时，才迁移到 PostgreSQL 或 broker-backed store；届时
替换 `RuntimeStore` 实现，而非改变 agent/channel/tool 的契约。

因此第一版的每个 MAGI workload 必须是 `replicas: 1`，使用独占 PVC；滚动更新时不能
有两个 Pod 同时打开同一个 SQLite 文件。部署应选 StatefulSet，或 Deployment 的
`Recreate` 策略；启动时执行 lease recovery，停止时先停止领取新 work。

## 消息与因果契约

所有内部消息至少应包含以下元数据：

```python
class AgentMessage:
    event_id: str                # inbox 去重与幂等
    kind: str                    # human.message, task.fire, tool.result, a2a.request, ...
    target_magic_id: str

    external_event_id: str | None  # Telegram update / A2A event 等外部去重键
    run_id: str | None
    conversation_id: str | None
    correlation_id: str          # 串起一个用户任务或跨 MAGI 对话
    causation_id: str | None     # 产生本消息的上游 message/effect
    reply_to: str | None         # 特别用于 tool_call_id / A2A invocation_id

    source_kind: str             # channel | tool | agent | system
    source_id: str | None
    idempotency_key: str
    priority: int
    deadline_at: datetime | None
    payload: dict
```

建议的首批 `kind`：

- `human.message`、`task.fire`、`a2a.request`：新的 agent 输入；
- `llm.response`、`tool.result`、`tool.failed`、`a2a.result`：异步 effect 的回执；
- `run.continue`：当 continuation 的前提已经满足时，由 runtime 写入的自消息；
- `run.cancel`、`run.timeout`：控制与恢复事件；
- `delivery.send`：写入 outbox 的出站投递 effect，而非另一个 agent 输入。

`correlation_id` 用于观察整个工作；`run_id` 找到可恢复 continuation；
`causation_id` 和 `reply_to` 防止迟到的 tool/A2A 结果串入错误 run。

## 分离的持久化队列

虽然第一版只使用一个 SQLite 文件，但不能把所有方向的数据塞进一张通用 `messages`
表，再由各组件猜测谁应消费它。建议的逻辑表及职责如下：

| 表 | 生产者 | 消费者 | 职责 |
| --- | --- | --- | --- |
| `agent_inbox` | channel、A2A receiver、tool worker、scheduler | Agent worker | 所有需要 agent 处理的输入 |
| `tool_jobs` | Agent worker | Tool worker | 待执行的工具调用 |
| `delivery_outbox` | Agent worker | Telegram/WebUI/A2A sender | 待投递的最终回复、进度或 A2A event |
| `agent_runs` | Agent worker | Agent worker | continuation、状态与 active run |
| `tool_calls` | Agent/Tool worker | Agent worker | tool 结果聚合与原始顺序 |
| `session_messages` | Agent transition | context builder、WebUI | 用户/模型可见 transcript，而非执行队列 |

最小 schema 语义：

- `agent_inbox`：`event_id` 唯一；`source_type + source_id + external_event_id` 在外部
  ID 存在时唯一；包含 `status`、`available_at`、`lease_owner`、`leased_until`、
  `attempts` 和 `last_error`。
- `tool_jobs`：`job_id`、`tool_call_id` 和 `idempotency_key` 唯一；保存参数、租约、
  执行状态和结果。
- `delivery_outbox`：`delivery_id`、原始 event ID 和 `idempotency_key` 唯一；保存
  channel、destination、payload、外部消息 ID、重试状态和租约。
- `agent_runs`：保存 `run_id`、`magic_id`、conversation/correlation、状态、版本号、
  iteration、deadline 与 continuation。
- `tool_calls`：保存 `tool_call_id`、`run_id`、`ordinal`、参数、状态、结果和错误。
  `ordinal` 用于按模型最初的 tool-call 顺序重建 transcript。

`session_messages` 必须逐步支持 provider-native blocks（assistant text/tool-use、
tool-result、provider metadata、tool-call ID），不能只保留扁平字符串；否则重启后无法
可靠构造 provider 合法的 tool-use → tool-result 上下文。

## 可恢复的 Agent 步骤

当前连续的 tool loop：

```text
LLM → await tool → tool result → LLM → ...
```

应演进为离散、持久化的步骤：

```text
human.message
  → agent transition
  → tool.requested (outbox)
  → tool worker
  → tool.result (inbox)
  → agent transition
  → run.continue
  → next LLM step
```

每个 agent transition 最多进行一次 LLM 推理；它绝不直接等待 tool 或远程 MAGI。
第一阶段可以让该单次推理由 Actor worker 直接 await，因为没有 channel 请求在等待它。
若未来需要将 LLM 限流、排队或跨进程执行，也可以将其进一步拆为 `llm.request` /
`llm.response` effect，而不改变 agent message 契约。

每个 run 至少需要持久化：

```text
run_id, magic_id, conversation_id, correlation_id
status, iteration_count, prompt/transcript projection
pending tool call ids, pending A2A invocation ids
created_at, updated_at, deadline_at, error
```

工具调用还需要独立的持久化记录：

```text
tool_call_id, run_id, tool_name, arguments, status
result/error, idempotency_key, requested_at, completed_at
```

这样在 tool 等待期间重启时，runtime 能恢复 continuation，而不会丢失 provider 所需的
`assistant tool_use → tool_result` 结构。

## Worker 生命周期、lease 与事务边界

`RuntimeStore` 是 execution 层对持久化的抽象；上层不应依赖 SQLite SQL 或名为
`SQLiteQueue` 的实现细节。其最小职责包括：发布 inbox、claim/complete/retry inbox、
原子提交 agent transition、claim/complete tool job、claim/complete delivery，以及启动时
的 lease recovery。

```python
class RuntimeStore(Protocol):
    async def publish_inbox(self, event: AgentMessage) -> PublishResult: ...
    async def claim_next_inbox(self, worker_id: str) -> AgentMessage | None: ...
    async def commit_agent_transition(self, transition: AgentTransition) -> None: ...
    async def fail_or_retry_inbox(self, event_id: str, error: str) -> None: ...
    async def claim_tool_job(self, worker_id: str) -> ToolJob | None: ...
    async def complete_tool_job(self, result: ToolResult) -> None: ...
    async def claim_delivery(self, channel_type: str, worker_id: str) -> Delivery | None: ...
    async def complete_delivery(self, delivery_id: str, external_id: str | None) -> None: ...
```

Agent worker 的一次消费遵循两个短事务：

```text
1. claim transaction
   BEGIN IMMEDIATE
   选择最早可处理的 pending/retry event
   标记 processing，写入 lease_owner / leased_until，attempts + 1
   COMMIT

2. agent step（无事务）
   读取 continuation；最多一次 LLM inference；不等待 tool、A2A 或 delivery

3. transition transaction
   保存 assistant/provider blocks
   更新 agent_runs / continuation / tool_calls
   创建 tool_jobs 和 delivery_outbox
   标记当前 inbox event completed
   COMMIT
```

Tool worker 也使用 claim → 事务外执行 → complete 的流程；但 complete transaction
必须同时更新 `tool_jobs`/`tool_calls`，并写入相应的 `tool.completed` 或 `tool.failed`
inbox message。这样工具结果不会在进程崩溃时丢失在两个状态之间。

运行时采取 at-least-once delivery + idempotent consumption：所有 worker 都使用短 lease、
有限重试、指数退避和 lease-expiry recovery。不可重复的外部副作用不能假装 exactly-once：
应将稳定 idempotency key 传给外部服务；若外部服务不支持，则记录风险并为高风险工具
设计人工确认或专门恢复策略。

发布顺序必须是“先提交 SQLite，再触发 `asyncio.Event`/`Condition` 唤醒”。内存唤醒可丢，
但 polling 与启动恢复扫描必须仍能发现持久化 pending work。每个并发 worker 使用独立
连接；连接维持 WAL、foreign keys、busy timeout 与有上限的 `SQLITE_BUSY` 重试。

### 多工具调用

同一次 LLM 响应产生多个 tool call 时：

1. 在同一事务中持久化全部 `tool.requested` effect，并将 run 标记为
   `waiting_tools`；
2. tool worker 可以并行执行；
3. 每个结果独立写回 inbox；
4. Actor 原子记录结果，但不在任意一个结果先到时立即续跑；
5. 全部完成、失败或超时后，按原始 tool-call 顺序组装结果，写入 `run.continue`；
6. 下一步 LLM 才看到完整、合法的 tool-result transcript。

这既保持并行，也避免 provider 因不完整 tool-result 序列拒绝请求。

### 第一版 mailbox 可处理性规则

第一版采用严格单 active-run 语义，以优先保证 provider transcript 的正确性：

- MAGI 空闲时，按 `available_at`、`created_at`、`id` 顺序领取下一条外部输入；
- MAGI 处于 `waiting_tools` 时，只处理该 active run 的 `tool.completed`、
  `tool.failed` 与 `run.cancel`；
- 新的用户消息、task 和 A2A request 留在 `pending`，不自动合并、取消当前工具，
  也不启动并行 run；
- 当前 run 完成、失败或取消后，才继续领取后续外部输入。

这是第一版的明确保守策略。以后可在有明确 transcript/cancel 语义后增加 interrupt，
但不应在初始实现中隐式插入用户消息或并行运行两个 conversation。

## A2A 语义

A2A 不应被限制为现有 `ChannelAdapter(uid, text)` 的简化出站模型。它是一种远程
MAGI invocation protocol，至少要支持 request、accepted、progress、result、failure 和
cancel。

```text
Agent A emits a2a.invoke
  → A outbox delivery worker sends request to B
  → B ingress durably writes a2a.request and returns 202 + invocation_id
  → B Actor processes independently
  → B emits a2a.result through its outbox
  → A ingress writes a2a.result to A inbox
  → A Actor continues later
```

因此 A 不会等待 B；A 的当前 transition 在写出 delegation effect 后结束。A2A 结果的
网络投递应具备重试和幂等，不能依赖一个仍然存活的 HTTP 调用栈。

## 持久化、投递与并发

第一阶段应使用每个 MAGI 的**私有 SQLite** 保存 inbox、outbox、runs 和 tool invocations。
这些都是 MAGI 私有执行状态，不应写入直属 MAGIS 的公共 PostgreSQL。外部 A2A 请求到达
目标 MAGI runtime 后，也先落入目标的私有 inbox。

消息系统按 at-least-once 设计，而不是承诺无法跨外部系统实现的 exactly-once：

- inbox 的 `event_id` / `idempotency_key` 唯一约束；
- 每条已领取消息具有 lease / visibility timeout；
- 状态变化与 outbox 写入使用 transactional outbox；
- tool invocation 与外部 delivery 使用稳定 idempotency key；
- worker 崩溃后可安全重试；
- 迟到结果依据 `run_id`、`reply_to` 与当前状态被接受、忽略或记录。

默认并发策略：

- 同一个 `magic_id`：一个 active Actor transition；
- 不同 MAGI：可并行；
- tools：可并行，但受 tool 类型、workspace 锁和 provider 限额约束；
- interactive input：高优先级；scheduled/proactive：低优先级；
- LLM 调用：独立 provider semaphore，避免账户级限流。

目前每个 MAGI 是一个容器和一个私有 workspace，SQLite mailbox 足以先建立正确语义。
只有当同一个 MAGI 需要多进程/多副本运行时，才评估 NATS、Redis Streams 或外部队列；
在此之前引入全局 broker 会增加部署与一致性成本。

## 建议的渐进实施顺序

### 阶段 0：调用路径审计、契约与验证样例

- 检查现有数据库模型、agent loop、Telegram、WebUI、tasks、tools 和 A2A 的实际
  调用路径与事务边界；
- 定义 `AgentMessage`、`Run`、`Effect` 和状态枚举；
- 写出并发、去重、lease 过期、崩溃恢复、多个 tool call、A2A result 迟到和用户
  插话的场景测试；
- 不修改现有运行路径。

### 阶段 1：Runtime contracts、SQLite schema 与恢复

- 在私有 SQLite 增加 inbox、tool jobs、outbox、runs 与 tool calls schema；
- 实现 publish、claim、complete、retry、lease recovery、polling/wakeup 与索引；
- 仅增加 store 和 worker 的验证，不改变 channel 或 agent 行为。

### 阶段 2：Compatibility worker，先解除 channel → agent 耦合

- WebUI、Telegram 和 task channel 改为发布 inbox message；
- Agent worker 暂时通过兼容 bridge 调用现有 `handle_message()`；
- 将最终回复写入 delivery outbox，暂时复用现有 dispatcher；
- 保持已有用户体验，同时验证单 Actor、inbox 和 delivery 语义。

### 阶段 3：单次 inference 的 `agent.step()` 与 continuation

- 将长生命周期 loop 拆为每次最多一次 LLM inference 的可恢复 step；
- 持久化 provider-native blocks、continuation 与 transcript projection；
- 删除依赖内存调用栈的等待逻辑。

### 阶段 4：异步 tool worker 与结果聚合

- Agent step 只创建 `tool_jobs`；tool worker 独立执行；
- tool result 通过 inbox 返回，按 ordinal 聚合后再 continuation；
- 加入 tool idempotency、timeout、retry 与 recovery 测试。

### 阶段 5：异步 A2A ingress/egress

- A2A request 采用 accept-then-process；
- 将结果、失败、进度和取消写回发起 MAGI inbox；
- 接入身份认证、MAGIS 权限与审计。

### 阶段 6：清理旧 loop、delivery 与运行治理

- WebUI 通过 SSE/WebSocket 订阅 run events；Telegram 投递最终回复和必要进度；
- 实现优先级、取消、限额、死信/人工重放和可观测性；
- 评估是否需要外部消息 broker。

## 待定问题

1. 单次 LLM 调用是否保留在 Actor transition 内，还是从第一版开始也变为
   `llm.request` / `llm.response`？建议第一版前者，保留后者的契约扩展点。
2. 哪些 tool 必须以 workspace/resource lock 串行？哪些可安全并行？
3. A2A 的身份、能力授权、回调/轮询和结果保留期分别如何定义？
4. run event 的保留、审计和隐私边界如何区分于普通 chat history？
5. 第二阶段以后是否需要显式 interrupt/cancel policy，而非严格 pending？

## 验证、观测与迁移门槛

每个阶段至少覆盖：SQLite 并发写与 `SQLITE_BUSY` 重试、inbox 去重、claim 后各崩溃点
的 lease recovery、乱序/重复 tool result、A2A commit 后 response 丢失、delivery 重试和
重启恢复。测试应确认所有 LLM、tool 与网络调用均在事务外。

至少记录 inbox/tool/outbox backlog、最老 pending age、claim-to-complete latency、SQLite
busy/retry、lease recovery、dead-letter、各类 attempts 以及 A2A 去重/重试次数。这些指标
是日后判断 SQLite 是否应被替换的依据，而不是预先引入 broker 的理由。

## 明确不采用的方案

- 不引入决定 MAGI 协作顺序的中心化 `TurnCoordinator`；
- 不把 A2A 业务依赖建模为 runtime graph 或 task board；
- 不让 Agent A 的调用栈同步等待 Agent B、tool 或 delivery；
- 不在第一阶段引入全局 broker 或把 MAGI 私有执行状态写入 MAGIS 公共数据库。

一句话概括：

> MAGI 不是“被控制器编排的一组 agent loop”，而是“各自拥有 durable mailbox 和
> 可恢复状态的 Actor；channel、tool 与其他 MAGI 都只是消息来源或消息去向”。
