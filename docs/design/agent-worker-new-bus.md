# AgentWorker 迁移到 NewBus 设计书

> 版本：v3.1（job-board-only）
> 日期：2026-08-08
> 状态：实施合同；以当前 `magi/agent/worker.py` 与 `magi/new_bus/guild/` 为准

## 1. 决策与边界

Agent 模块采用 **job board 直调**模型。`AgentWorker` 是一个顺序消费者和
协调者；各 board 是唯一的持久化协调点。

以下是不可突破的边界：

- `AgentWorker` 只注入 `NewBus`；不得导入或调用 `magi.bus`、旧 store、旧
  runtime singleton。
- 不新增 `AgentTurnStore`、`AgentTurnBook`、`agent_turns` 或
  `agent_messages`，也不把 turn continuation 做成另一套持久化状态机。
- 不新增进程内 steering 队列。steering 消息保留在 `agent_job_board`，由
  `claim_for_conversation()` 认领。
- 不废弃 `BaseJobBoard.release()`。它是“主循环先认领到同会话新消息”与
  “当前 turn 在等待期间将其作为 steering 重新认领”之间的必要交接。
- 不为旧 `magi.bus` 的表、字段、Worker 或 HTTP 协议保留双写、回退读取或
  兼容包装。最终 cutover 使用 NewBus 自己的数据库/迁移；表名不是旧运行时的
  兼容承诺。

`agent_inbox`、`llm_jobs`、`tool_jobs`、`delivery_outbox` 和
`a2a_invocations` 是当前 NewBus board 的本地持久化实现。它们的 ORM 只可由
`magi.new_bus` 持有；业务模块只能使用 DTO 和 board 方法。若最终新库要调整
物理表名，必须在 NewBus migration 中一次完成，不能与旧 Bus 共用 ORM 或做
双写。

## 2. 架构

```text
Channel / Task / A2A ingress
        |
        | publish(ChatJob)
        v
 agent_job_board (chatJobBoard)
        |
        | claim()
        v
   AgentWorker
        |
        +-- publish(CallLLMJob) --> llm_job_board --> ProvidersWorker
        |         ^ get_result()
        |
        +-- publish(RunToolJob) --> tool_job_board --> ToolsWorker
        |         ^ get_result()
        |
        +-- publish(SendA2AJob) --> a2a_job_board --> A2AWorker
        |         ^ get_result()
        |
        +-- publish(DeliveryJob) --> delivery_job_board --> channel worker
        |
        `-- submit_result(ChatJobResult) --> agent_job_board
```

每个 board 都遵循同一生命周期：

```text
publish -> pending -> claim/processing -> submit_result -> completed | failed
                         |
                         `-> lease 过期后由 BaseJobBoard claim 回收，最多 3 次
```

lease、重试耗尽和 result 的持久化由 `BaseJobBoard` 负责。Agent 不额外维护
turn lease、CAS turn 状态或跨 board 的事务提交。

## 3. NewBus 合同

| NewBus 字段 | DTO | Agent 侧动作 | 执行者 |
| --- | --- | --- | --- |
| `agent_job_board` | `ChatJob` / `ChatJobResult` | `claim`、`release`、`claim_for_conversation`、`submit_result` | `AgentWorker` |
| `llm_job_board` | `CallLLMJob` / `CallLLMResult` | `publish`、`get_result` | `ProvidersWorker` |
| `tool_job_board` | `RunToolJob` / `RunToolResult` | `publish`、`get_result` | `ToolsWorker` |
| `a2a_job_board` | `SendA2AJob` / `SendA2AResult` | `publish`、`get_result` | `A2AWorker` |
| `delivery_job_board` | `DeliveryJob` / `DeliveryResult` | `publish` | 各 channel delivery worker |

`AgentWorker` 还只读以下 Books：`settings_book`、`sessions_book`、
`messages_book`、`tool_definitions_book`、`tool_catalog_book`、
`token_usage_book`，以及系统提示词所需的 NewBus Books。Books 不承担 job
协调职责。

### 3.1 输入与结果 DTO

`ChatJob` 的稳定字段为：

```python
ChatJob(
    event_id=source_idempotency_key,
    run_id=run_id,
    conversation_id=conversation_id,
    correlation_id=correlation_id,
    kind="chat",                 # 取消使用 "run.cancel"
    payload={
        "text": text,
        "channel": channel,
        "uid": uid,
        "session_id": session_id,
        "caller_role": caller_role,
    },
)

ChatJobResult(
    event_id=job.event_id,
    success=True,
    status="completed",
    result={"run_id": job.run_id},
    error_code=None,
)
```

`ChatJobResult` 只表达该输入是否处理完成；回复正文不塞入 result，而是作为
`DeliveryJob.payload["text"]` 交给 channel worker 投递。

LLM、工具与 A2A 的关联键分别是 board 返回的 `job_id`、`RunToolJob.tool_call_id`
和 `SendA2AJob.invocation_id`。不要假定不存在的 `job_id`、`metadata` 或
`target_magic_id` 字段，也不要为 Agent 增加第二套 DTO。

## 4. AgentWorker 生命周期

`AgentWorker(bus, poll_seconds=0.25)` 单进程顺序消费 `agent_job_board`。其
进程内状态仅用于当前运行：

- `_active_sessions: set[str]` 标识正在处理的 conversation；
- `_in_flight: dict[str, asyncio.Event]` 将取消通知传给当前 `_process()`；
- `RunContext` 保存当前提示词消息、最终文本和取消标志。

这些状态不是持久化恢复协议。进程崩溃后，尚未 `submit_result()` 的 job 由
board 的 lease 回收；已投递到其他 board 的 job 由各自 board 继续执行。这个
模型的承诺是 board 级 durability，而不是跨 LLM、工具、投递和 transcript 的
原子 turn transaction。

### 4.1 主循环

```text
claim ChatJob
  ├─ cancel：标记该 cancel job 成功，并对相同 conversation 的 in-flight Event 置位
  ├─ conversation 已 active：release(job)，等待当前 turn 的 steering 轮询认领
  └─ 新 conversation：建立 RunContext，执行 _process(ctx)
       └─ finally: submit_result(root ChatJobResult)
```

`release()` 会把仍处于 `processing` 的 job 放回 `pending`，并归还这次
claim 的 attempts；因此不得移除、废弃或用“根 turn lease”替代它。

### 4.2 Agent loop

`_process(ctx)` 的每轮执行顺序固定为：

1. 从 `sessions_book/messages_book` 加载既有 history；
2. 用 system prompt、history 和 `tool_definitions_book` 组装 `CallLLMJob`；
3. `llm_job_board.publish()` 后轮询 `get_result()`；
4. 没有 tool use 时，发布 `DeliveryJob`，可异步请求生成标题，结束；
5. 有 tool use 时，拆成 `RunToolJob` 或 `SendA2AJob`，逐个 publish；
6. 轮询各结果，同时认领 steering；把结果和 steering 组成下一轮 user message；
7. 达到最大迭代次数、超时、LLM 失败或取消时，发布对应 delivery，再结束。

LLM、工具、A2A 和 channel I/O 全部在 board worker 外执行；Agent 只发布和
轮询 job，不直接调用这些执行器。

## 5. Steering 与取消

### 5.1 Steering

当某个 conversation 正在等待 LLM、工具或 A2A 时，新来的 `ChatJob` 会发生：

```text
_run() claim(new ChatJob)
  -> conversation in _active_sessions
  -> agent_job_board.release(event_id)
  -> _gather_all() 的下一轮
  -> claim_for_conversation(conversation_id)
  -> 读取 payload.text，submit_result(success=True)
  -> 文本加入下一轮 LLM 的 user message
```

`claim_for_conversation()` 必须保持 SQLite 的 compare-and-set 认领语义：按
conversation 的最旧 pending/过期 processing job 选择，再以 status 与 lease
条件 UPDATE；抢占失败则重选。这是 board 内部并发控制，不是新的 Agent turn
状态机。

当前单 MAGI `AgentWorker` 是顺序消费者，`_active_sessions` 也是进程内集合。
因此本阶段不声称多 AgentWorker 横向并发时的 conversation 独占。若将来需要
横向扩展，必须单独设计 `chatJobBoard` 的 conversation-level claim 语义和验证
方案；不得私自引入 `AgentTurnStore`。

### 5.2 取消

取消 job 的契约是 `ChatJob.kind == "run.cancel"`，并使用同一
`conversation_id` 指向运行中的会话。Agent 接收后：

1. 完成 cancel job；
2. 设置该 conversation 的 `cancel_event`；
3. `_process()` 在 LLM 等待前后和效果收集循环中观察它，投递“任务已取消”。

截至本文版本，`worker.py` 仍从 `job.metadata["kind"]` 判断取消，而 `ChatJob`
没有 `metadata` 字段。这是实现偏差，必须在迁移实现阶段改为读取 `job.kind`；
文档不把该错误描述为既有 API，也不因此增加 metadata 兼容层。

## 6. Effects、错误与幂等

### 6.1 Tool / A2A

普通 tool use 发布 `RunToolJob(tool_name, payload, tool_call_id, ... )`。
`message_magi` 在 `_A2A_ENABLED` 打开后发布 `SendA2AJob`；关闭时按普通工具
返回稳定的 `a2a_disabled` 验证错误。`expect_reply=True` 的 A2A 结果与普通
工具结果一样，按 `tool_call_id` 回填下一轮 prompt。

Agent 只在 board 返回 terminal result 后继续。超时的工具被转换为
`RunToolResult(success=False, is_error=True)`，交给 LLM 决定后续处理。

### 6.2 Delivery

所有结束路径通过：

```python
delivery_job_board.publish(DeliveryJob(
    channel=ctx.channel,
    payload={"text": reply, "session_id": ctx.session_id, "uid": ctx.uid},
    destination=None,
))
```

Channel worker 是唯一负责渠道 API 写入的组件。`ChatJobResult` 成功不等于
外部渠道已经送达；送达状态由 `delivery_job_board` 自己的 result 表达。

### 6.3 当前 durability 边界

当前 board API 的 `publish()` 与另一个 board 的 `submit_result()` 不是一个
事务。实施时必须为每个入站 channel 使用稳定 `event_id`，为投递和工具调用
选择稳定的业务 idempotency key，并把重复 delivery/工具效果视为需要在目标
worker 消解的风险。这里不能通过虚构 `commit_terminal()` 等跨表 API 掩盖该
边界。

## 7. 实施顺序

1. **冻结 job-board 合同**：对照 guild DTO 和 `BaseJobBoard`，删除所有
   `AgentTurnStore`、额外 turn 表、lease-renew、cancel-CAS 的设计/实现残留。
2. **完成 Agent 模块迁移**：`worker.py` 与 `agent_context`、`system_prompt`、
   `compaction`、`auto_title`、`token_usage` 只通过注入的 NewBus 工作；修正
   `job.kind` cancel 判定和 DTO 字段不一致。
3. **完成 Channel 与启动迁移**：所有 ingress 只发布 `ChatJob`；所有 egress
   只消费 `DeliveryJob`；composition root 只注入同一个 `NewBus`。
4. **一次 cutover**：为 NewBus 建立独立 schema/migration 和状态目录，切换
   runtime，删除旧 Bus worker、旧表访问、双写与兼容测试。历史数据只作离线
   归档，不进入新运行时读路径。
5. **最后统一验证**：仅在上述实施全部完成后再执行完整测试、迁移验证和
   端到端 smoke。实施中的检查仅限静态 import/编译/文档一致性，不做“改一段
   跑一段”的功能测试。

## 8. 最终验收

统一验证阶段至少覆盖：

- 任何 Agent 模块均不导入 `magi.bus`；
- Channel 发布 `ChatJob`，Agent 成功写 `ChatJobResult` 并发布 `DeliveryJob`；
- 普通回复、LLM 失败、LLM 超时、工具成功/超时、最大迭代和取消；
- 活动会话中的 steering 可被 release 后重新认领，不产生独立 reply；
- `message_magi` 分别在 feature gate 关闭/开启时走正确 board；
- 进程中断后未完成 board job 能在 lease 过期后回收；
- 新运行时不访问旧 Bus 代码、表或兼容 API。

## 9. 多 Agent 协作约定

修改 public DTO、board 方法、Worker 生命周期或 storage schema 前，先在本节
追加一条同步记录，再改代码；记录必须包含日期、责任人、受影响合同和是否需要
更新本设计书。没有同步记录时，不得以“顺手重构”为由新增持久化层或废弃其他
worker 仍依赖的方法。

### 同步记录

- 2026-08-08 / Codex：删除文档中误引入的 `AgentTurnStore`、`AgentTurnBook`、
  `agent_turns`、`agent_messages`、`renew_lease`、`cancel()` 和跨表原子
  commit 设计；恢复并明确 job-board-only 模型、`release()` 与
  `claim_for_conversation()` 的既有职责。未修改生产代码，未运行功能测试。
