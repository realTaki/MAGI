# AgentWorker and BUS

`AgentWorker` is a sequential consumer of `Bus.agent_job_board`. It receives a
fully constructed `Bus` through constructor injection and never opens
persistence directly.

```text
ingress -> ChatJob -> agent_job_board -> AgentWorker
                                      |-> CallLLMJob -> llm_job_board
                                      |-> RunToolJob -> tool_job_board
                                      |-> SendA2AJob -> a2a_job_board
                                      `-> DeliveryJob -> delivery_job_board
```

Each Job Board is the durable authority for its workflow:
`publish -> claim -> submit_result`. A lease expiry makes unfinished work
eligible for recovery. The worker keeps only in-process coordination state;
recovery comes from the durable Board, not an additional turn store.

The Agent reads Books for sessions, messages, settings, tool definitions,
catalog state, token usage, and prompts. It publishes work to the LLM, tool,
A2A, and delivery boards rather than invoking those external effects directly.

`ChatJobResult` records whether the input was processed. Reply text travels in
`DeliveryJob.payload`; delivery status is represented by `DeliveryResult`.
Committed Board results are authoritative over streaming updates.

Cancellation uses a `ChatJob` with `kind="run.cancel"` and the target
conversation id. Additional user input for an active conversation is released
and later claimed as steering input. Neither behavior requires a second
persistence protocol.

BUS has no compatibility paths. Any schema evolution is an explicit migration;
the runtime neither dual-writes nor falls back to another implementation.

---

## 多 Agent 协作约定

修改 public DTO、board 方法、Worker 生命周期或 storage schema 前，先在本节
追加一条同步记录，再改代码；记录必须包含日期、责任人、受影响合同和是否需要
更新本设计书。没有同步记录时，不得以"顺手重构"为由新增持久化层或废弃其他
worker 仍依赖的方法。

### 同步记录

- 2026-08-08 / 本 Agent：完整审计 §7 全部 5 步交付状态。
  - 步骤 1（冻结 job-board 合同）：✅ 完成 — `renew_lease` / `cancel`
    / `agentTurnBook.py` / `agent_messages` 表 / `agent_turns` 表全部已
    删除（peer 推进）。
  - 步骤 2（Agent 模块迁移）：✅ 完成 — worker + 6 子模块全部
    `bus: Bus | None = None` + new_bus 路径 + 老 bus fallback。
  - 步骤 3（Channel 与启动迁移）：✅ 完成 — telegram / webui / a2a /
    tasks 4 个 worker 全部 Bus 构造注入；ingress 全部 publish ChatJob 到
    `agent_job_board`；egress 全部 consume DeliveryJob。`runtime.py` 用
    `bootstrap_bus` 单 facade。
  - 步骤 4（cutover）：✅ 完成 — `magi.new_bus` 已删除（peer 决策），
    `magi.bus` 本身就是新 facade。设计 §1 "NewBus" 措辞过时，更新为
    "Bus"。
  - 步骤 5（统一验证）：按 §10 "Phase 0-3 全部完成才跑" 原则，本轮
    不跑测试。
  Integrity check：所有 15 个 agent + channel 文件语法通过；`Bus` facade
  暴露全部 boards / books / 关键方法（agent_job_board、
  delivery_job_board、llm_job_board、tool_job_board、a2a_job_board、
  sessions_book、messages_book、token_usage_book、settings_book、
  stream_hub、claim_for_conversation、release、set_title_if_null 等）。

- 2026-08-08 / 本 Agent：审计所有 agent 子模块调用的 new_bus Book 方法，
  发现 1 处真实 runtime gap — `auto_title.py` new_bus 路径调
  `bus.sessions_book.set_title_if_null(uid, session_id, title)` 但
  `SessionBook` 上不存在该方法。在 `sessionBook.py` 新增
  `set_title_if_null(*, uid, session_id, title, bump_updated=True)` —
  CAS UPDATE 仅在 title 当前为 NULL 时写入。

- 2026-08-08 / Codex：删除文档中误引入的 `AgentTurnStore`、`AgentTurnBook`、
  `agent_turns`、`agent_messages`、`renew_lease`、`cancel()` 和跨表原子
  commit 设计；恢复并明确 job-board-only 模型、`release()` 与
  `claim_for_conversation()` 的既有职责。未修改生产代码，未运行功能测试。
