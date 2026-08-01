# MAGI 单 Agent 事件驱动 Runtime 设计方案

> 版本：v1.1  
> 状态：Phase 1–4 的核心执行链已落地；streaming、delivery outbox sender 与 A2A transport 仍为后续实现方案。
> 本次更新：active run steering、Actor 内完整 LLM 调用、流式输出

## 1. 目标

将 MAGI 当前由 Channel 直接调用长生命周期 Agent Loop 的实现，迁移为单 Agent、事件驱动、可恢复的 Actor Runtime。

目标包括：

- Channel、Tool、scheduled task 和其他 Agent 只负责发布事件；
- 每个 Agent 拥有独立、持久化的 mailbox 和运行状态；
- 同一个 Agent 串行推进状态，Tool 和出站投递可以并行执行；
- LLM、Tool、Telegram、WebUI 和 A2A 网络调用均在数据库 transaction 外执行；
- Agent 在一次 step 内直接完成一次 LLM 调用，不把 LLM 拆成独立的持久化 request/response Worker；
- LLM 调用支持流式输出，同时以最终提交到 SQLite 的完整 response 作为权威结果；
- active run 等待 Tool 时，同一 conversation 的新消息作为 steering input 加入当前 run，不取消 Tool，也不新开 run；
- 进程崩溃或重启后，可以从 SQLite 恢复未完成状态；
- 保持 Runtime 与具体数据库、Channel 和业务编排逻辑解耦。

## 2. 总体架构

第一版采用：

```text
SQLite = 唯一持久化事实来源
asyncio.Event/Condition = 本进程低延迟唤醒
定时轮询 SQLite = 重启恢复和防止唤醒丢失
A2A HTTP = Agent 之间的网络传输
StreamHub = 本进程流式增量分发，不作为事实来源
```

暂时不要引入 Redis Streams、RabbitMQ、NATS 或 Kafka，也不要为 LLM 单独引入持久化消息队列。

### 2.1 主要数据流

```text
Channel / A2A / Scheduler
          ↓
     agent_inbox
          ↓
     Agent Worker
       ├─→ LLMGateway.stream() ─→ StreamHub ─→ WebUI/Channel
       ├─→ tool_jobs ─→ Tool Workers ─→ agent_inbox
       └─→ delivery_outbox ─→ Channel/A2A Senders
```

### 2.2 执行边界

一次 Agent step 的逻辑边界是：

```text
领取一个可处理事件
→ 准备 continuation/context
→ 最多进行一次完整 LLM inference
→ 流式转发增量
→ 原子提交最终状态变化
```

“一次 step 最多一次 LLM 调用”是逻辑执行边界，不表示 LLM 和 SQLite 处于同一个事务中。

## 3. 选择 SQLite 的理由

### 3.1 当前写并发很低

虽然 Channel、Tool Worker 和 Agent Worker 都会写数据库，但写事务本身很短。系统的大部分时间消耗在：

- LLM inference；
- 外部 HTTP；
- Tool execution；
- Telegram/A2A delivery。

这些操作必须在 SQLite transaction 外执行。因此 SQLite 的单 writer 模型不会成为当前主要瓶颈。

### 3.2 可以获得真正的原子状态转移

Agent 完成一次 inference 后，需要同时完成：

- 保存 assistant message；
- 更新 continuation；
- 创建 tool jobs 或 outbound deliveries；
- 将当前 inbox event 标记为完成。

使用一个 SQLite transaction 可以保证这些操作全部成功或全部失败。

如果引入独立消息队列，就会出现：

```text
SQLite 状态提交成功，但消息没有写入 broker
```

或者：

```text
消息已经进入 broker，但 SQLite 状态提交失败
```

届时仍然需要 transactional outbox，整体反而更复杂。

### 3.3 部署更简单

每个 Agent 已经有自己的 SQLite。继续使用它不需要：

- 新增基础设施；
- 配置 broker 集群；
- 管理额外认证；
- 处理 broker 与数据库的双写；
- 为本地开发模拟分布式组件。

### 3.4 保留未来替换能力

业务代码不能直接依赖 SQLite SQL，应通过 BusStore 接口访问。

未来需要多副本或更高吞吐量时，可以替换为 PostgreSQL 或 broker-backed 实现，而不修改 Agent、Channel 和 Tool 的核心逻辑。

## 4. 核心原则

### 4.1 一个 Agent 串行消费

同一个 Agent 同时最多推进一个 active run，并且最多执行一个 `agent.step()`。

Tool jobs 和 outbound deliveries 可以并行执行，但它们的结果必须重新进入 Agent mailbox，由 Agent 串行处理。

### 4.2 所有外部操作都在 transaction 外执行

禁止在 SQLite transaction 中执行：

- LLM inference 或读取 LLM stream；
- Tool execution；
- Telegram API 请求；
- A2A HTTP 请求；
- WebSocket/SSE 网络发送；
- 任何可能长时间等待的操作。

数据库 transaction 只覆盖短时间的读取、插入和状态更新。

### 4.3 至少一次投递与幂等消费

系统采用：

```text
at-least-once delivery + idempotent consumption
```

不要声称能够实现端到端 exactly-once。

以下情况都可能导致重试：

- Agent 在 LLM 返回后、数据库提交前崩溃；
- Tool 已经产生外部副作用，但结果尚未提交；
- A2A 接收方已经提交，HTTP response 却丢失；
- Telegram 已经发送成功，但 sender 没有收到确认。

因此所有事件、Tool job、LLM attempt 和 delivery 都必须有稳定的幂等或关联 ID。

### 4.4 Bus 不做业务编排

Bus 不能决定：

- 哪个 Agent 应该完成任务；
- Agent 之间如何分工；
- 多个 Agent 的结果如何合并；
- 下一个业务步骤是什么；
- 是否建立任务 graph；
- 自然语言消息是否代表取消、改目标或新任务。

这些决策应由 Agent 或明确的产品控制信号完成。

Runtime 只允许实现必要的执行约束，例如：

- 一个 Agent 串行处理；
- 保持 provider-valid transcript；
- 等待同一次模型输出产生的全部 tool results；
- 将同一 conversation 的后续消息附加到 active run；
- 超时、重试、死信和崩溃恢复。

### 4.5 新消息默认 steering，不打断 active run

active run 期间，同一 conversation 到达的新用户消息：

1. 立即持久化；
2. 关联当前 `run_id`；
3. 作为 steering input 等待进入下一次模型输入；
4. 不自动取消正在执行的 Tool；
5. 不创建并行 run；
6. 不要求旧目标先输出 final answer。

如果当前存在尚未闭合的 tool calls，必须先写入对应 tool results，再把 steering messages 放在其后。

### 4.6 LLM 不拆成离散 Worker

第一版由 Agent Worker 在 `agent.step()` 内直接调用 `LLMGateway.stream()`。不要建立：

```text
llm_requests
llm_responses
LLM Worker
```

拆分并不能使 LLM 与 SQLite 获得 exactly-once，也不会改变单 active run 必须等待 inference 完成的事实。

只有未来出现独立 GPU 调度、跨进程集中限流或独立推理服务队列等明确需求时，才考虑替换 `LLMGateway` 的实现。

## 5. 不要使用一张万能 messages 表

仍然只使用一个 SQLite 文件，但必须将不同方向的持久化队列分开。

建议至少包含以下表：

| 表 | 生产者 | 消费者 | 职责 |
|---|---|---|---|
| `agent_inbox` | Channel、A2A receiver、Tool Worker、scheduler | Agent Worker | 所有需要 Agent 处理的输入 |
| `tool_jobs` | Agent Worker | Tool Worker | 待执行的工具调用 |
| `delivery_outbox` | Agent Worker | Channel/A2A sender | 待投递的权威出站消息 |
| `agent_runs` | Agent Worker | Agent Worker | continuation 和当前运行状态 |
| `run_inputs` | Channel/Agent Worker | Agent Worker | active run 期间到达的 steering inputs |
| `tool_calls` | Agent Worker、Tool Worker | Agent Worker | 工具调用及结果聚合 |
| `a2a_invocations` | Agent Worker、A2A sender/receiver | Agent Worker | 委派请求、回执与 continuation 关联 |
| `llm_attempts` | Agent Worker | Agent Worker、WebUI | 推理尝试与流式生命周期，不保存逐 token 队列 |
| `session_messages` | Agent Worker | LLM context builder、WebUI | 模型及用户可见 transcript |

不要让所有模块往一张表中写入记录，再通过 `message_type` 猜测由谁处理。

## 6. 数据模型建议

字段名可根据现有 ORM 和命名规范调整，但语义必须保留。

### 6.1 `agent_inbox`

```text
id
event_id                 UNIQUE
event_type
source_type              channel | agent | tool | task | system
source_id
external_event_id
conversation_id
run_id
target_run_id
correlation_id
causation_id
payload_json

status                    pending | processing | completed | retry | dead
available_at
lease_owner
leased_until
attempts
last_error

received_seq
created_at
started_at
completed_at
```

建议增加幂等约束：

```text
UNIQUE(event_id)
UNIQUE(source_type, source_id, external_event_id)
```

第二个唯一约束在 `external_event_id` 存在时生效，用于处理 Telegram update、A2A event 等重复投递。

`received_seq` 表示真实收件顺序，不等于 provider context 中的排列顺序。

### 6.2 `tool_jobs`

```text
id
job_id                    UNIQUE
run_id
tool_call_id
tool_name
arguments_json
idempotency_key           UNIQUE

status                    pending | processing | completed | failed | retry | dead
available_at
lease_owner
leased_until
attempts
last_error
result_json

created_at
started_at
completed_at
```

同一次 LLM response 产生多个 tool calls 时，可以创建多个 `tool_jobs`，由 Tool Worker 并行执行。

### 6.3 `delivery_outbox`

```text
id
delivery_id               UNIQUE
event_id                  UNIQUE
channel_type              telegram | webui | a2a
destination_json
payload_json
idempotency_key           UNIQUE

status                    pending | processing | delivered | retry | dead
available_at
lease_owner
leased_until
attempts
last_error
external_message_id

created_at
started_at
delivered_at
```

`delivery_outbox` 保存需要可靠投递的 committed message，不要为每个流式 token 创建一条 outbox record。

### 6.4 `agent_runs`

```text
run_id                    PRIMARY KEY
conversation_id
status                    ready | running | waiting_tools | waiting_a2a |
                          completed | failed | cancelled
continuation_json
expected_tool_call_ids
expected_a2a_invocation_ids
iteration_count
token_usage
deadline_at
version
created_at
updated_at
```

`continuation_json` 必须保存重启后继续推理所需的状态，不能依赖内存中的 Python 对象。

### 6.5 `run_inputs`

```text
id
input_id                  UNIQUE
run_id
conversation_id
source_event_id           UNIQUE
received_seq
context_seq
input_type                steer | control
content_json
status                    pending | attached | consumed
created_at
attached_at
consumed_at
```

语义：

- 新消息到达时立即获得 `received_seq`；
- 只有被加入 provider transcript 时才获得 `context_seq`；
- `pending` 表示已关联 active run，但尚未进入模型输入；
- `attached/consumed` 的具体划分可以按 ORM 简化，但必须支持幂等恢复。

同一 active run 中多个 steering messages 按 `received_seq` 排列，并在全部 tool results 之后进入下一次 LLM input。

### 6.6 `tool_calls`

```text
tool_call_id              PRIMARY KEY
run_id
ordinal
tool_name
arguments_json
status
result_json
error_json
created_at
completed_at
```

`ordinal` 用于按照模型最初生成 tool calls 的顺序重建 provider-valid transcript。

### 6.7 `a2a_invocations`

```text
invocation_id             PRIMARY KEY
run_id
target_magic_id
request_event_id          UNIQUE
reply_to
status                    pending | accepted | completed | failed | timed_out | cancelled
idempotency_key           UNIQUE
deadline_at
result_json
error_json
created_at
accepted_at
completed_at
```

`202 Accepted` 只更新为 `accepted`，绝不代表委派完成。若该 invocation 是当前 run
继续推理的必要条件，`agent_runs` 记录它的 ID 并进入 `waiting_a2a`；返回的 A2A event
必须以 `reply_to=invocation_id` 关联回正确 run。

### 6.8 `llm_attempts`

```text
llm_attempt_id            PRIMARY KEY
run_id
inbox_event_id
provider
model
status                    started | streaming | completed | interrupted | failed
last_stream_seq
usage_json
error_json
started_at
completed_at
```

`llm_attempts` 用于关联流式事件、诊断中断和前端去重。它不保存每个 token，也不是 LLM request/response 队列。

第一版可以只持久化 attempt 的开始与终态；如需短时断线续传，可定期批量保存 draft checkpoint，但不能把 draft 当成最终 continuation。

### 6.9 `session_messages`

继续保存用户和模型可见的会话记录，但它不是执行队列。

应保存 provider-native 内容，包括：

- assistant text blocks；
- tool-use blocks；
- tool-result blocks；
- user steering messages；
- provider message metadata；
- tool call ID 和原始顺序；
- `received_seq` 和 `context_seq`。

不要只保存扁平化后的字符串，否则重启后可能无法构造合法的工具调用上下文。

## 7. Bus 接口

定义与具体数据库无关的接口。名称可以根据现有代码风格调整。

```python
class BusStore(Protocol):
    async def publish_inbox(
        self,
        event: AgentEvent,
    ) -> PublishResult: ...

    async def claim_next_inbox(
        self,
        worker_id: str,
    ) -> AgentEvent | None: ...

    async def attach_run_input(
        self,
        event: AgentEvent,
        run_id: str,
    ) -> None: ...

    async def commit_agent_transition(
        self,
        transition: AgentTransition,
    ) -> None: ...

    async def fail_or_retry_inbox(
        self,
        event_id: str,
        error: str,
    ) -> None: ...

    async def claim_tool_job(
        self,
        worker_id: str,
    ) -> ToolJob | None: ...

    async def complete_tool_job(
        self,
        result: ToolResult,
    ) -> None: ...

    async def claim_delivery(
        self,
        channel_type: str,
        worker_id: str,
    ) -> Delivery | None: ...

    async def complete_delivery(
        self,
        delivery_id: str,
        external_message_id: str | None,
    ) -> None: ...
```

SQLite 只是这个接口的第一种实现，例如：

```text
bus/contracts.py
bus/store.py
bus/sqlite_store.py
bus/wakeup.py
bus/recovery.py
bus/stream.py
```

不要创建名为 `SQLiteQueue` 的接口，避免上层代码绑定实现细节。

## 8. Agent Event Contract

所有进入 Agent mailbox 的输入使用统一 envelope：

```python
@dataclass
class AgentEvent:
    event_id: str
    event_type: str

    source_type: str
    source_id: str
    external_event_id: str | None

    conversation_id: str
    run_id: str | None
    target_run_id: str | None
    correlation_id: str
    causation_id: str | None

    payload: dict
    created_at: datetime
```

第一版至少支持：

```text
channel.message.received
agent.message.received
run.steer
run.cancel
tool.completed
tool.failed
task.triggered
system.retry
system.cancel
```

Channel 可以始终发布 `channel.message.received`。由 Runtime 在短事务中根据同一 `conversation_id` 是否存在 active run，将消息附加为 `run.steer` 语义；不要依赖对“算了，换个方法”之类自然语言做分类。

显式 Stop 按钮或控制 API 才产生 `run.cancel/system.cancel`。Event contract 应做版本控制，Channel 特有字段放在 payload 中。

## 9. Agent Worker 生命周期

Agent Worker 只允许存在一个逻辑消费者。

### 9.1 领取事件

使用短事务：

```text
BEGIN IMMEDIATE

选择最早且当前可处理的 pending/retry 事件
设置：
  status = processing
  lease_owner = 当前 worker
  leased_until = 当前时间 + lease duration
  attempts += 1

COMMIT
```

SQLite 版本允许时，可以使用 `UPDATE ... RETURNING` 原子领取。

领取完成后立即结束事务，再执行 Agent step。

### 9.2 调用 Agent 与 LLM

```python
transition = await agent.step(
    event=event,
    continuation=current_run,
    stream=stream_sink,
)
```

`agent.step()`：

- 最多调用一次 LLM；
- 通过 `LLMGateway.stream()` 完成完整 inference；
- 不在数据库事务内调用 LLM；
- 不等待外部工具执行；
- 不等待另一个 Agent 回复；
- 累积并返回完整 provider response；
- 返回需要持久化的状态变化和后续事件。

### 9.3 提交状态转移

LLM 返回后开启新的短事务，一次性提交：

```text
保存完整 assistant/provider message
更新 agent_runs/continuation
创建 tool_jobs
创建 delivery_outbox
更新 tool_calls
消费已纳入本次 input 的 run_inputs
将当前 agent_inbox 事件标记 completed
将 llm_attempt 标记 completed
```

全部成功后 commit，再发出 `message.committed` 流式控制事件。

如果进程在 LLM 返回后、commit 前崩溃，lease 到期后可以重试该 inbox event。LLM 可能再次调用，但不应出现半提交的 continuation 或重复 outbox 记录。

## 10. Mailbox 与 steering 可处理性规则

第一版采用严格的单 active run 语义，但允许同一 conversation 的消息继续修改当前 run。

### 10.1 Agent 空闲时

按 `available_at, created_at, id` 顺序领取下一条外部输入，并创建新的 run。

### 10.2 Agent 正在进行 LLM inference 时

Channel 仍可并发接收并持久化新消息，但 Agent Worker 不会中断当前 HTTP stream，也不会同时启动第二次 inference。

当前 inference 完成并提交后，新消息作为当前 run 的 pending steering input，在下一次可推进点进入模型上下文。

### 10.3 Agent 正在等待 tools 时

允许处理与 active run 相关的：

```text
run.steer
run.cancel
tool.completed
tool.failed
system.cancel
```

同一 conversation 的普通新消息默认作为 `run.steer`：

- 立即写入 `run_inputs`；
- 关联 active `run_id`；
- 完成该 inbox event，但不立即调用 LLM；
- 不取消正在执行的 Tool；
- 等全部 expected tool calls 进入终态后再继续。

其他 conversation 的消息继续留在 `pending`，直到 active run 完成。

### 10.4 Provider transcript 顺序

当 tool results 与 steering messages 都已就绪时，下一次 LLM input 必须按以下顺序构造：

```text
User：原任务
Assistant：tool_call(s)
Tool：result(s)，按原始 ordinal
User：active run 期间收到的 steering message(s)，按 received_seq
Assistant：根据新目标继续
```

不能构造为：

```text
Assistant：tool_call
User：算了，换个方法
Tool：result
```

也不需要先让模型针对旧目标生成 final answer，再处理 steering message。

### 10.5 取消是独立的显式控制语义

普通消息不会自动打断 Tool。只有显式 Stop 按钮、取消 API 或明确的系统控制事件才产生 `run.cancel`。

取消时仍需让所有 tool calls 进入 completed、failed 或 cancelled 终态，并生成 provider-valid tool results。对于已经产生外部副作用的 Tool，必须记录真实结果，不能伪装成 cancelled。

## 11. Tool 调用流程

```text
Agent Worker
  → 保存 assistant tool-use blocks
  → 创建 tool_calls
  → 创建 tool_jobs
  → run 状态改为 waiting_tools
  → 当前 step 结束

Tool Worker
  → 领取 tool_job
  → transaction 外执行 tool
  → 保存结果
  → 向 agent_inbox 写入 tool.completed/tool.failed

Channel（可并发）
  → active run 期间收到新消息
  → 保存为 run.steer/run_input
  → 不取消 tool

Agent Worker
  → 串行消费 tool result 和 steering events
  → 更新 tool_calls/run_inputs
  → expected tool calls 未全部结束：不调用 LLM
  → 全部结束：按 ordinal 重建 tool-result blocks
  → 在 tool results 后追加 steering messages
  → 进行下一次流式 LLM inference
```

Tool Worker 完成工具时，下面两项必须在同一个 transaction 内提交：

```text
更新 tool_job/tool_call 状态
写入对应的 agent_inbox 事件
```

### 11.1 多个 Tool 并行

一次模型输出产生多个 tool calls 时，所有 calls 都必须进入终态，才能恢复 LLM。结果按照 `ordinal` 重建，不按照完成时间排列。

期间收到的多个 steering messages 统一放在全部 tool results 之后，并按 `received_seq` 排列。

### 11.2 Tool 幂等

每个 tool job 使用稳定的 `idempotency_key`。

对于有外部副作用的工具：

- 优先将 idempotency key 传给外部服务；
- 如果外部服务不支持幂等，必须记录重试风险；
- 不要假设“Tool 已执行但数据库提交前崩溃”能够自动做到 exactly-once；
- 高风险、不可重复的操作应有专门的恢复或人工确认策略。

## 12. LLM 调用与流式输出

### 12.1 Gateway 接口

```python
class LLMGateway(Protocol):
    async def stream(
        self,
        request: LLMRequest,
        on_event: Callable[[LLMStreamEvent], Awaitable[None]],
    ) -> LLMResponse:
        ...
```

Provider adapter 将 OpenAI、Anthropic 等原生流事件统一映射为：

```text
llm.started
llm.text.delta
llm.tool_arguments.delta
llm.usage.updated
llm.completed
llm.failed
message.committed
```

这些是流式生命周期和展示事件，不是 Agent 必须从 durable mailbox 逐条消费的 Runtime events。

### 12.2 StreamHub

```text
Provider SSE stream
   ├─ delta → StreamHub → WebUI SSE/WebSocket
   │                    → Telegram throttled message edits（可选）
   └─ accumulate → 完整 provider response → SQLite final commit
```

`StreamHub` 可以使用本进程 pub/sub、async iterator 或 callback。它负责低延迟分发，不负责最终一致性。

### 12.3 可靠性边界

流式 delta 是临时、best-effort 的；SQLite 中 committed final message 才是权威结果。

每个流事件至少携带：

```text
run_id
llm_attempt_id
sequence_number
```

前端以这些字段去重和拼接。最终事务成功后才发送 `message.committed`。

如果 inference 中途崩溃：

- 当前 `llm_attempt` 标记为 `interrupted`；
- 已发送 delta 无法回滚，UI 应标记为未提交草稿；
- lease recovery 使用新的 `llm_attempt_id` 重试；
- 新 attempt 不与旧 attempt 的 delta 混合；
- 只有新 attempt 的 committed response 进入 transcript。

### 12.4 不要逐 token 持久化

禁止：

- 每个 token 写一次 SQLite；
- 每个 token 创建一条 `delivery_outbox`；
- 让 Agent mailbox 消费每个 delta；
- 把未完成 draft 当成 provider continuation。

如果产品需要断线续传，可以按时间或字符数批量保存 draft checkpoint，但这属于可选优化。

### 12.5 Channel 适配

- WebUI：优先 SSE 或 WebSocket；
- Telegram：可以不流式，或按节流策略编辑一条 provisional message；
- A2A：第一版只可靠投递 committed message，不向远端发送逐 token delta；
- 最终 committed reply 仍通过 `delivery_outbox` 可靠投递。

## 13. Channel 流程

### 13.1 入站

Telegram、WebUI 和 scheduled tasks 不再调用 `handle_message()`。

它们只负责构造并发布事件：

```python
await runtime.publish_inbox(
    AgentEvent(
        event_id=event_id,
        event_type="channel.message.received",
        source_type="channel",
        source_id="telegram",
        external_event_id=str(update_id),
        conversation_id=conversation_id,
        run_id=None,
        target_run_id=None,
        correlation_id=event_id,
        causation_id=None,
        payload={
            "text": text,
            "user_id": user_id,
            "chat_id": chat_id,
            "message_id": message_id,
        },
        created_at=utcnow(),
    )
)
```

在同一个短事务中：

- 若 conversation 空闲，事件保持为普通待处理输入；
- 若 conversation 存在 active run，将其关联到 `target_run_id` 并建立 `run_input`；
- 不进行自然语言意图分类；
- 不取消 Tool 或 LLM stream。

只有 SQLite commit 成功后，Channel 才能认为消息已被接受。

### 13.2 出站

Agent 的最终 reply 写入 `delivery_outbox`。

不同 sender 只领取属于自己的 delivery：

```text
Telegram sender → Telegram Bot API
WebUI sender/projection → committed message API
A2A sender → 对方 /a2a/inbox
```

实时 delta 由 `StreamHub` 发送，最终消息由 outbox 保证可靠投递。这两条路径通过 `run_id + llm_attempt_id` 关联。

网络发送必须在 transaction 外执行。发送成功后再以短事务标记为 `delivered`。

## 14. A2A 可靠投递

不同 Agent 的 SQLite 完全独立：

```text
Agent A SQLite
  delivery_outbox
        ↓
Agent A A2A Sender
        ↓ HTTP POST
Agent B A2A Receiver
        ↓
Agent B SQLite
  agent_inbox
```

流程：

1. Agent A 生成 `agent.message`。
2. A 在自己的 `delivery_outbox` 中保存消息。
3. A2A sender 使用稳定的 `event_id` 向 B 发送 HTTP POST。
4. B 通过 `event_id` 唯一约束执行幂等插入。
5. B 必须在本地 SQLite commit 成功后返回 `202 Accepted`。
6. A 收到 `202` 后将 delivery 标记为 `delivered`。
7. 如果 HTTP response 丢失，A 可以重发。
8. B 对重复 `event_id` 返回成功，但不能再次插入或执行。

A2A HTTP response 只确认“消息已持久化接收”，不能等待 B 完成 LLM 推理。

B 的回复是一个新的、带 correlation metadata 的 A2A event。若 A 的当前 run 正在等待
B 的结果，回复还必须携带 `reply_to=invocation_id`；A 的 Actor 只恢复该匹配 run。这样
“请求已被 B durable 接收”和“B 已产生可用结果”是两个可恢复、可审计的状态，而不是
一个同步 RPC 调用栈。

A2A 消息属于 Agent 能力，不要在 Runtime 中把它解释成 task graph 或同步 RPC。第一版 A2A 不传输逐 token stream。

## 15. SQLite 配置

初始化连接时至少设置：

```sql
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;
```

同时遵守：

- 每个并发 worker 使用独立数据库连接；
- transaction 尽可能短；
- `SQLITE_BUSY` 使用有上限的指数退避重试；
- lease 到期的 `processing` 任务可以恢复为 `retry`；
- 为 `status + available_at` 建立索引；
- 为所有幂等 ID 建立唯一索引；
- 为 `run_inputs(run_id, status, received_seq)` 建立索引；
- 定期清理或归档 completed records；
- 不允许多个 Pod 通过 NFS 等共享文件系统同时打开同一个 SQLite。

## 16. Worker 唤醒

SQLite 没有 `LISTEN/NOTIFY`，因此采用双机制：

```text
持久化：SQLite
即时唤醒：asyncio.Event/Condition
兜底：短周期 polling
```

发布事件的顺序必须是：

```text
先 commit SQLite
再触发内存唤醒
```

如果进程在 commit 后、唤醒前崩溃，polling 和启动恢复扫描仍能发现事件。

内存 Queue/Event 和 StreamHub 不能成为事实来源。重启后允许丢失唤醒和未提交 delta，但不能丢失持久化事件或 committed message。

## 17. Kubernetes 约束

每个 Agent workload 第一版必须满足：

```text
replicas: 1
```

推荐使用：

- 独占 PVC；
- StatefulSet，或者 Deployment 的 `Recreate` 策略；
- 启动时执行 lease 和 interrupted LLM attempt recovery；
- shutdown 时停止领取新任务，并尽量释放当前 lease；
- StreamHub 只服务当前 Pod 的客户端连接。

不要让滚动更新期间两个 Pod 同时写同一个 SQLite 文件。

如果未来要求同一个 Agent 多副本并行处理，应优先迁移到 PostgreSQL，并重新设计 StreamHub，而不是继续扩展共享 SQLite。

## 18. 包边界

建议形成如下依赖关系：

```text
magi/
├── bus/         # SQLite durable bus：contracts、mailbox、lease、retry、recovery、StreamHub
├── agent/       # reasoning、context、单次 step、AgentWorker、LLM gateway
├── channels/    # Telegram、WebUI、A2A ingress/delivery workers
├── tools/       # tool definitions、ToolWorker 和 executors
├── proactive/   # 产生 bus events 的 policy
└── db/          # ORM、migration、SQLite implementation
```

依赖方向：

```text
channels/tools/proactive → bus contracts
agent worker → bus + agent.step
tool worker → bus + tool.run
channel workers → bus
agent → LLM gateway、memory、tool schemas、bus contracts
bus StreamHub → channel stream sinks
```

禁止：

```text
channels → agent.handle_message
tools → agent loop
agent → Telegram/WebUI/A2A implementation
LLM gateway → agent mailbox per-token events
```

## 19. 迁移步骤

不要一次性删除现有 `handle_message()`。按以下阶段迁移，并确保每个阶段可运行、可测试。

### Phase 1：Bus contracts 与 SQLite schema

- 定义 AgentEvent、Transition 和 Store interface；
- 增加 inbox、tool jobs、outbox、runs、run inputs、tool calls、A2A invocations 和 LLM attempts 表；
- 配置 WAL、busy timeout 和索引；
- 实现 publish、claim、complete、retry 和 lease recovery；
- 增加并发、去重、lease 过期和 crash recovery 测试。

### Phase 2：Compatibility Worker

- 增加 Agent Worker；
- Worker 暂时调用现有 `handle_message()`；
- Telegram、WebUI、scheduled tasks 改为发布 inbox event；
- 最终回复通过 delivery outbox 发送；
- 保证现有用户功能不变。

这一阶段的目的是先解除 Channel 与 Agent 的直接调用关系。

### Phase 3：单次 inference 的 `agent.step()` 与 streaming

- 将长生命周期 Agent Loop 拆成可恢复 step；
- 一次 step 最多调用一次 LLM；
- LLM 仍在 Actor 内通过 Gateway 完成，不引入 LLM Worker；
- 接入 provider stream normalization 和 StreamHub；
- 持久化 provider-native assistant/tool blocks；
- 持久化 continuation 和 LLM attempt 终态；
- 明确 delta 与 committed message 的边界；
- 删除依赖内存调用栈的等待逻辑。

### Phase 4：异步 Tool Worker 与 steering

- Agent step 只创建 tool jobs；
- Tool Worker 独立执行；
- Tool result 通过 agent inbox 返回；
- 实现多 tool call 聚合；
- 同一 conversation 的新消息附加到 active run；
- Tool 继续执行，不因普通 steering message 被取消；
- tool results 后按顺序追加 steering messages；
- 加入 tool idempotency、timeout、retry 和 recovery 测试。

### Phase 5：异步 A2A

- A2A receiver 只负责持久化接收并返回 `202`；
- A2A sender 消费 delivery outbox；
- 实现 event ID 去重和重试；
- 持久化 `a2a_invocations` 与 `waiting_a2a` continuation；
- 删除 HTTP 请求内调用远端 Agent Loop 的行为。

### Phase 6：清理旧 Loop

- 删除所有 Channel 对 `handle_message()` 的直接依赖；
- 删除同步等待 Tool/A2A 的旧路径；
- 统一 transcript ownership；
- 删除失去用途的兼容代码。

## 20. 测试要求

至少覆盖：

### 20.1 SQLite 并发

- Channel、Tool Worker 和 Agent Worker 同时写入；
- LLM streaming 期间 Channel 仍可持久化消息；
- 不出现永久 `database is locked`；
- `SQLITE_BUSY` 能够有限重试；
- transaction 中没有外部 await。

### 20.2 Inbox 幂等

- 相同 Telegram/A2A event 投递两次；
- 数据库只存在一个逻辑事件；
- 同一 steering message 不会重复加入 provider context；
- Agent 只推进一次已提交状态。

### 20.3 Agent crash recovery

- 事件领取后、LLM 调用前崩溃；
- LLM streaming 中崩溃；
- LLM 返回后、transition commit 前崩溃；
- transition commit 后、worker ack 前崩溃；
- lease 到期后都能恢复，不产生重复 outbox record；
- 新 LLM attempt 不会与旧 attempt 的 delta 混合。

### 20.4 Steering

- `waiting_tools` 时收到一条和多条同 conversation 新消息；
- 新消息立即持久化并关联 active run；
- 普通新消息不会取消 Tool；
- 不会创建并行 run；
- tool results 全部位于 steering messages 之前；
- steering messages 按 `received_seq` 排列；
- 新消息进入下一次 inference，而不是等待旧目标 final answer；
- 其他 conversation 的消息继续排队。

### 20.5 Tool recovery

- 多个 tools 并行完成；
- tool results 乱序到达；
- Agent 按原始 ordinal 重建 transcript；
- tool result 重复投递不会重复恢复 run；
- tool timeout/failure 能进入 Agent；
- steering 到达时 Tool 仍正常完成并记录真实结果。

### 20.6 Streaming

- text 和 tool arguments delta 能按 sequence 转发；
- 前端能按 `run_id + llm_attempt_id + sequence_number` 去重；
- commit 前断线不会生成权威 final message；
- commit 后发出 `message.committed`；
- 不逐 token 写 SQLite/outbox；
- WebUI 重连后能从 committed transcript 恢复；
- Telegram 节流编辑失败不影响最终 outbox delivery。

### 20.7 A2A

- 接收方 commit 后 response 丢失；
- 发送方重试；
- 接收方根据 event ID 去重；
- 返回 `202` 时消息已经持久化；
- HTTP 请求不等待 Agent 推理。
- `202 accepted` 不会被误当成 A2A result；只有匹配 `reply_to` 的结果可恢复等待中的 run。

### 20.8 Delivery

- 发送失败后重试；
- 进程重启后 pending delivery 仍能恢复；
- delivered records 不会被普通 recovery 重复领取。

## 21. 可观测性

为后续判断 SQLite 和当前 Actor 边界是否仍然适合，至少记录：

- inbox pending 数；
- 最老 pending event 的等待时间；
- active run 的 pending steering input 数；
- tool job/outbox backlog；
- claim-to-complete latency；
- LLM time-to-first-token、stream duration 和 attempt status；
- interrupted/retried LLM attempt 数；
- SQLite busy 次数和重试次数；
- lease expiration/recovery 次数；
- 每类任务的 attempts；
- dead-letter 数量；
- A2A 重试和去重次数。

只有出现以下需求时才考虑替换 SQLite 或拆分 LLM：

- 同一个 Agent 需要多个 Pod 同时运行；
- 多台机器需要共享同一个 Agent mailbox；
- SQLite busy/retry 已经形成持续瓶颈；
- 单机队列吞吐量明显不足；
- 需要集中式跨 Agent event stream；
- 需要独立 GPU 推理队列；
- 需要跨进程集中限流或模型调度。

## 22. 非目标

本次设计不包括：

- Redis、RabbitMQ、NATS 或 Kafka 部署；
- 独立持久化的 `llm.request/llm.response` Worker；
- workflow graph；
- central coordinator；
- 外部任务编排器；
- Agent 自动分工策略；
- 多 Agent 共享数据库；
- 同一个 Agent 的并行 conversation execution；
- 普通自然语言消息自动取消 Tool；
- A2A 逐 token streaming；
- 端到端 exactly-once 承诺。

## 23. 验收标准

- [ ] 每个 Agent 仍然只需要自己的 SQLite。
- [ ] Channel 不再直接调用 Agent Loop。
- [ ] Tool execution 不再阻塞 Agent 的 Python 调用栈。
- [ ] A2A receiver 在持久化消息后立即返回，不等待推理。
- [ ] 同一 Agent 同时最多执行一个 step 和一个 active run。
- [ ] LLM 在 Actor step 内完成，不拆成独立 LLM Worker。
- [ ] LLM 支持 stream，delta 不逐 token 持久化。
- [ ] 最终完整 LLM response 提交后才成为权威 transcript。
- [ ] LLM、Tool 和网络操作全部位于 transaction 外。
- [ ] Agent transition 与其生成的 jobs/outbox 在一个 transaction 中提交。
- [ ] active run 期间同一 conversation 的新消息立即持久化为 steering input。
- [ ] 普通 steering input 不取消 Tool，不新建并行 run。
- [ ] 所有 tool results 闭合后，steering messages 紧接在其后进入下一次 LLM input。
- [ ] steering 不要求旧目标先生成 final answer。
- [ ] `202 Accepted` 不被视为 A2A invocation 的完成；等待 A2A 结果的 run 可在重启后恢复。
- [ ] 所有 inbox、tool jobs 和 deliveries 都支持 lease、retry 和 recovery。
- [ ] 所有跨边界事件都有稳定幂等 ID。
- [ ] 重启后能够从 SQLite 恢复 pending/processing 状态。
- [ ] Bus 不包含 Agent 分工、自然语言意图分类或业务流程决策。
- [ ] 上层代码只依赖 BusStore contract，不直接依赖 SQLite 实现。

## 24. 最终设计原则

> 每个 MAGI Agent 是一个独立 actor，拥有自己的 SQLite durable mailbox 和可恢复状态。Channel、Tool、scheduled task 和其他 Agent 都只是事件来源；Agent 串行消费事件并产生新的持久化事件。active run 期间同一 conversation 的新消息作为 steering input 被立即持久化，但不会打断正在执行的 Tool；在所有 tool results 闭合后，这些消息紧接其后进入下一次模型输入。LLM 由 Actor 在一次 step 内直接以流式方式完成，增量通过 StreamHub best-effort 分发，最终完整 response 经 SQLite 提交后成为权威结果。SQLite 负责单 Agent 内部的一致性，A2A HTTP 负责 Agent 之间的可靠投递。
